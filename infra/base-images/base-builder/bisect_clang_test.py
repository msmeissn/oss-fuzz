"""Tests for bisect_clang.py"""
import os
from unittest import mock
import unittest

import bisect_clang

FILE_DIRECTORY = os.path.dirname(__file__)


def patch_environ(testcase_obj):
  """Patch environment."""
  env = {}
  patcher = mock.patch.dict(os.environ, env)
  testcase_obj.addCleanup(patcher.stop)
  patcher.start()


class BisectClangTestMixin:
  """Useful mixin for bisect_clang unittests."""

  def setUp(self):
    patch_environ(self)
    os.environ['SRC'] = '/src'
    os.environ['WORK'] = '/work'


class GetClangBuildEnvTest(BisectClangTestMixin, unittest.TestCase):
  """Tests for get_clang_build_env."""

  def test_cflags(self):
    """Test that CFLAGS are not used compiling clang."""
    os.environ['CFLAGS'] = 'blah'
    self.assertNotIn('CFLAGS', bisect_clang.get_clang_build_env())

  def test_cxxflags(self):
    """Test that CXXFLAGS are not used compiling clang."""
    os.environ['CXXFLAGS'] = 'blah'
    self.assertNotIn('CXXFLAGS', bisect_clang.get_clang_build_env())

  def test_other_variables(self):
    """Test that other env vars are used when compiling clang."""
    key = 'other'
    value = 'blah'
    os.environ[key] = value
    self.assertEqual(value, bisect_clang.get_clang_build_env()[key])


def read_test_data(filename):
  with open(os.path.join(FILE_DIRECTORY, 'test_data', filename)) as f:
    return f.read()


class SearchBisectOutputTest(BisectClangTestMixin, unittest.TestCase):
  """Tests for search_bisect_output."""

  def test_search_bisect_output(self):
    """Test that search_bisect_output finds the responsible commit when one
    exists."""
    test_data = read_test_data('culprit-commit.txt')
    self.assertEqual('ac9ee01fcbfac745aaedca0393a8e1c8a33acd8d',
                     bisect_clang.search_bisect_output(test_data))

  def test_search_bisect_output_none(self):
    """Test that search_bisect_output doesnt find a non-existent culprit
    commit."""
    self.assertIsNone(bisect_clang.search_bisect_output('hello'))


def create_mock_popen(
    output=bytes('', 'utf-8'), err=bytes('', 'utf-8'), returncode=0):
  """Creates a mock subprocess.Popen."""

  class MockPopen:
    """Mock subprocess.Popen."""
    commands = []
    testcases_written = []

    def __init__(self, command, *args, **kwargs):  # pylint: disable=unused-argument
      """Inits the MockPopen."""
      stdout = kwargs.pop('stdout', None)
      self.command = command
      self.commands.append(command)
      self.stdout = None
      self.stderr = None
      self.returncode = returncode
      if hasattr(stdout, 'write'):
        self.stdout = stdout

    def communicate(self, input_data=None):  # pylint: disable=unused-argument
      """Mock subprocess.Popen.communicate."""
      if self.stdout:
        self.stdout.write(output)

      if self.stderr:
        self.stderr.write(err)

      return output, err

    def poll(self, input_data=None):  # pylint: disable=unused-argument
      """Mock subprocess.Popen.poll."""
      return self.returncode

  return MockPopen


class BuildClangTest(BisectClangTestMixin, unittest.TestCase):
  """Tests for build_clang."""

  def test_build_clang_test(self):
    """Tests that build_clang works as intended."""
    with mock.patch('subprocess.Popen', create_mock_popen()) as mock_popen:
      bisect_clang.build_clang()
      self.assertEqual([['bash', '/src/checkout_llvm.sh'],
                        ['ninja', '-C', '/work/llvm-stage2', 'install']],
                       mock_popen.commands)


class GitRepoTest(BisectClangTestMixin, unittest.TestCase):
  """Tests for GitRepo."""
  # TODO(metzman): Mock filesystem. Until then, use a real directory.
  REPO_DIR = '/tmp'

  def setUp(self):
    super().setUp()
    self.git = bisect_clang.GitRepo(self.REPO_DIR)
    self.good_commit = 'good_commit'
    self.bad_commit = 'bad_commit'
    self.test_command = 'testcommand'

  def test_do_command(self):
    """Test do_command creates a new process as intended."""
    # TODO(metzman): Test directory changing behavior.
    command = ['subcommand', '--option']
    with mock.patch('subprocess.Popen', create_mock_popen()) as mock_popen:
      self.git.do_command(command)
      self.assertEqual([['git', 'subcommand', '--option']], mock_popen.commands)

  def _test_test_start_commit_unexpected(self, label, commit, returncode):
    """Tests test_start_commit works as intended when the test returns an
    unexpected value."""

    def mock_execute(command, *args, **kwargs):  # pylint: disable=unused-argument
      if command == self.test_command:
        return returncode, '', ''
      return 0, '', ''

    with mock.patch('bisect_clang.execute', mock_execute):
      with self.assertRaises(bisect_clang.BisectException):
        self.git.test_start_commit(commit, label, self.test_command)

  def test_test_start_commit_bad_zero(self):
    """Tests test_start_commit works as intended when the test on the first bad
    commit returns 0."""
    self._test_test_start_commit_unexpected('bad', self.bad_commit, 0)

  def test_test_start_commit_good_nonzero(self):
    """Tests test_start_commit works as intended when the test on the first good
    commit returns nonzero."""
    self._test_test_start_commit_unexpected('good', self.good_commit, 1)

  def test_test_start_commit_good_zero(self):
    """Tests test_start_commit works as intended when the test on the first good
    commit returns 0."""
    self._test_test_start_commit_expected('good', self.good_commit, 0)  # pylint: disable=no-value-for-parameter

  @mock.patch('bisect_clang.build_clang')
  def _test_test_start_commit_expected(self, label, commit, returncode,
                                       mocked_build_clang):
    """Tests test_start_commit works as intended when the test returns an
    expected value."""
    command_args = []

    def mock_execute(command, *args, **kwargs):  # pylint: disable=unused-argument
      command_args.append(command)
      if command == self.test_command:
        return returncode, '', ''
      return 0, '', ''

    with mock.patch('bisect_clang.execute', mock_execute):
      self.git.test_start_commit(commit, label, self.test_command)
      self.assertEqual([['git', 'checkout', commit], self.test_command,
                        ['git', 'bisect', label]], command_args)
      mocked_build_clang.assert_called_once_with()

  def test_test_start_commit_bad_nonzero(self):
    """Tests test_start_commit works as intended when the test on the first bad
    commit returns nonzero."""
    self._test_test_start_commit_expected('bad', self.bad_commit, 1)  # pylint: disable=no-value-for-parameter

  @mock.patch('bisect_clang.GitRepo.test_start_commit')
  def test_bisect_start(self, mock_test_start_commit):
    """Tests bisect_start works as intended."""
    with mock.patch('subprocess.Popen', create_mock_popen()) as mock_popen:
      self.git.bisect_start(self.good_commit, self.bad_commit,
                            self.test_command)
      self.assertEqual(['git', 'bisect', 'start'], mock_popen.commands[0])
      mock_test_start_commit.assert_has_calls([
          mock.call('bad_commit', 'bad', 'testcommand'),
          mock.call('good_commit', 'good', 'testcommand')
      ])

  def test_do_bisect_command(self):
    """Test do_bisect_command executes a git bisect subcommand as intended."""
    subcommand = 'subcommand'
    with mock.patch('subprocess.Popen', create_mock_popen()) as mock_popen:
      self.git.do_bisect_command(subcommand)
      self.assertEqual([['git', 'bisect', subcommand]], mock_popen.commands)

  @mock.patch('bisect_clang.build_clang')
  def _test_test_commit(self, label, output, returncode, mocked_build_clang):
    """Test test_commit works as intended."""
    command_args = []

    def mock_execute(command, *args, **kwargs):  # pylint: disable=unused-argument
      command_args.append(command)
      if command == self.test_command:
        return returncode, output, ''
      return 0, output, ''

    with mock.patch('bisect_clang.execute', mock_execute):
      result = self.git.test_commit(self.test_command)
      self.assertEqual([self.test_command, ['git', 'bisect', label]],
                       command_args)
    mocked_build_clang.assert_called_once_with()
    return result

  def test_test_commit_good(self):
    """Test test_commit labels a good commit as good."""
    self.assertIsNone(self._test_test_commit('good', '', 0))  # pylint: disable=no-value-for-parameter

  def test_test_commit_bad(self):
    """Test test_commit labels a bad commit as bad."""
    self.assertIsNone(self._test_test_commit('bad', '', 1))  # pylint: disable=no-value-for-parameter

  def test_test_commit_culprit(self):
    """Test test_commit returns the culprit"""
    test_data = read_test_data('culprit-commit.txt')
    self.assertEqual('ac9ee01fcbfac745aaedca0393a8e1c8a33acd8d',
                     self._test_test_commit('good', test_data, 0))  # pylint: disable=no-value-for-parameter
