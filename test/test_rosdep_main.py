# Copyright (c) 2012, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the Willow Garage, Inc. nor the names of its
#       contributors may be used to endorse or promote products derived from
#       this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from contextlib import contextmanager
import os
import sys
try:
    from cStringIO import StringIO
except ImportError:
    from io import StringIO

import rospkg

import unittest
from unittest.mock import DEFAULT, patch

from rosdep2 import create_default_installer_context
from rosdep2 import main
from rosdep2.ament_packages import AMENT_PREFIX_PATH_ENV_VAR
from rosdep2.main import rosdep_main
from rosdep2.main import setup_proxy_opener


GITHUB_BASE_URL = 'https://github.com/ros/rosdistro/raw/master/rosdep/base.yaml'
GITHUB_PYTHON_URL = 'https://github.com/ros/rosdistro/raw/master/rosdep/python.yaml'


def get_test_dir():
    return os.path.abspath(os.path.dirname(__file__))


def get_test_tree_dir():
    return os.path.abspath(os.path.join(get_test_dir(), 'tree'))


def get_test_catkin_tree_dir():
    return os.path.abspath(os.path.join(get_test_tree_dir(), 'catkin'))


def get_cache_dir():
    p = os.path.join(get_test_dir(), 'sources_cache')
    assert os.path.isdir(p)
    return p


@contextmanager
def fakeout():
    realstdout = sys.stdout
    realstderr = sys.stderr
    fakestdout = StringIO()
    fakestderr = StringIO()
    sys.stdout = fakestdout
    sys.stderr = fakestderr
    yield fakestdout, fakestderr
    sys.stdout = realstdout
    sys.stderr = realstderr

# the goal of these tests is only to test that we are wired into the
# APIs.  More exhaustive tests are at the unit level.


class TestRosdepMain(unittest.TestCase):

    def setUp(self):
        if 'ROSDEP_DEBUG' in os.environ:
            del os.environ['ROSDEP_DEBUG']
        self.old_rr = rospkg.get_ros_root()
        self.old_rpp = rospkg.get_ros_package_path()
        self.old_app = os.getenv(AMENT_PREFIX_PATH_ENV_VAR, None)
        if 'ROS_ROOT' in os.environ:
            del os.environ['ROS_ROOT']
        os.environ['ROS_PACKAGE_PATH'] = os.path.join(get_test_tree_dir())
        os.environ[AMENT_PREFIX_PATH_ENV_VAR] = os.path.join(get_test_tree_dir(), 'ament')
        if 'ROS_PYTHON_VERSION' not in os.environ:
            # avoid `test_check` failure due to warning on stderr
            os.environ['ROS_PYTHON_VERSION'] = sys.version[0]

    def tearDown(self):
        if self.old_rr is not None:
            os.environ['ROS_ROOT'] = self.old_rr
        if self.old_rpp is not None:
            os.environ['ROS_PACKAGE_PATH'] = self.old_rpp
        if self.old_app is not None:
            os.environ[AMENT_PREFIX_PATH_ENV_VAR] = self.old_app

    def test_bad_commands(self):
        sources_cache = get_cache_dir()
        cmd_extras = ['-c', sources_cache]
        for commands in [[], ['fake', 'something'], ['check'], ['install', '-a', 'rospack_fake'],
                         ['check', 'rospack_fake', '--os', 'ubuntulucid'],
                         ]:
            try:
                rosdep_main(commands + cmd_extras)
                assert False, 'system exit should have occurred'
            except SystemExit:
                pass

    def test_check(self):
        sources_cache = get_cache_dir()
        cmd_extras = ['-c', sources_cache]

        with fakeout() as b:
            try:
                rosdep_main(['check', 'python_dep'] + cmd_extras)
            except SystemExit:
                assert False, 'system exit occurred: %s\n%s' % (b[0].getvalue(), b[1].getvalue())

            stdout, stderr = b
            assert stdout.getvalue().strip() == 'All system dependencies have been satisfied', stdout.getvalue()
        try:
            context = create_default_installer_context()
            override = '%s:%s' % context.get_os_name_and_version()
            with fakeout() as b:
                rosdep_main(['check', 'python_dep', '--os', override] + cmd_extras)
                stdout, stderr = b
                assert stdout.getvalue().strip() == 'All system dependencies have been satisfied'
        except SystemExit:
            assert False, 'system exit occurred'

        # this used to abort, but now rosdep assumes validity for even empty stack args
        try:
            with fakeout() as b:
                rosdep_main(['check', 'packageless'] + cmd_extras)
                stdout, stderr = b
                assert stdout.getvalue().strip() == 'All system dependencies have been satisfied'
        except SystemExit:
            assert False, 'system exit occurred'

        try:
            rosdep_main(['check', 'nonexistent'] + cmd_extras)
            assert False, 'system exit should have occurred'
        except SystemExit:
            pass

    @patch('rosdep2.platforms.debian.read_stdout')
    @patch('rosdep2.installers.os.geteuid', return_value=1)
    def test_install(self, mock_geteuid, mock_read_stdout):
        sources_cache = get_cache_dir()
        cmd_extras = ['-c', sources_cache]
        catkin_tree = get_test_catkin_tree_dir()

        def read_stdout(cmd, capture_stderr=False):
            if cmd[0] == 'apt-cache' and cmd[1] == 'showpkg':
                result = ''
            elif cmd[0] == 'dpkg-query':
                if cmd[-1] == 'python3-dev':
                    result = "'python3-dev install ok installed\n'"
                else:
                    result = '\n'.join(['dpkg-query: no packages found matching %s' % f for f in cmd[3:]])

            if capture_stderr:
                return result, ''
            return result

        try:
            mock_read_stdout.side_effect = read_stdout
            # python must have already been installed
            with fakeout() as b:
                rosdep_main(['install', 'python_dep'] + cmd_extras)
                stdout, stderr = b
                assert 'All required rosdeps installed' in stdout.getvalue(), stdout.getvalue()
            with fakeout() as b:
                rosdep_main(['install', 'python_dep', '-r'] + cmd_extras)
                stdout, stderr = b
                assert 'All required rosdeps installed' in stdout.getvalue(), stdout.getvalue()
            with fakeout() as b:
                rosdep_main([
                    'install', '-s', '-i',
                    '--os', 'ubuntu:lucid',
                    '--rosdistro', 'fuerte',
                    '--from-paths', catkin_tree
                ] + cmd_extras)
                stdout, stderr = b
                expected = [
                    '#[apt] Installation commands:',
                    '  sudo -H apt-get install ros-fuerte-catkin',
                    '  sudo -H apt-get install libboost1.40-all-dev',
                    '  sudo -H apt-get install libeigen3-dev',
                    '  sudo -H apt-get install libtinyxml-dev',
                    '  sudo -H apt-get install libltdl-dev',
                    '  sudo -H apt-get install libtool',
                    '  sudo -H apt-get install libcurl4-openssl-dev',
                ]
                lines = stdout.getvalue().splitlines()
                assert set(lines) == set(expected), lines
        except SystemExit:
            assert False, 'system exit occurred: ' + b[1].getvalue()
        try:
            rosdep_main(['install', 'nonexistent'])
            assert False, 'system exit should have occurred'
        except SystemExit:
            pass

    def test_where_defined(self):
        try:
            sources_cache = get_cache_dir()
            expected = GITHUB_PYTHON_URL
            for command in (['where_defined', 'testpython'], ['where_defined', 'testpython']):
                with fakeout() as b:
                    # set os to ubuntu so this test works on different platforms
                    rosdep_main(command + ['-c', sources_cache, '--os=ubuntu:lucid'])
                    stdout, stderr = b
                    output = stdout.getvalue().strip()
                    assert output == expected, output
        except SystemExit:
            assert False, 'system exit occurred'

    def test_what_needs(self):
        try:
            sources_cache = get_cache_dir()
            cmd_extras = ['-c', sources_cache]
            expected = ['python_dep']
            with fakeout() as b:
                rosdep_main(['what-needs', 'testpython'] + cmd_extras)
                stdout, stderr = b
                output = stdout.getvalue().strip()
                assert output.split('\n') == expected
            expected = ['python_dep']
            with fakeout() as b:
                rosdep_main(['what_needs', 'testpython', '--os', 'ubuntu:lucid', '--verbose'] + cmd_extras)
                stdout, stderr = b
                output = stdout.getvalue().strip()
                assert output.split('\n') == expected
        except SystemExit:
            assert False, 'system exit occurred'

    def test_keys(self):
        sources_cache = get_cache_dir()
        cmd_extras = ['-c', sources_cache]

        try:
            with fakeout() as b:
                rosdep_main(['keys', 'rospack_fake'] + cmd_extras)
                stdout, stderr = b
                assert stdout.getvalue().strip() == 'testtinyxml', stdout.getvalue()
                assert not stderr.getvalue(), stderr.getvalue()
            with fakeout() as b:
                rosdep_main(['keys', 'rospack_fake', '--os', 'ubuntu:lucid', '--verbose'] + cmd_extras)
                stdout, stderr = b
                assert stdout.getvalue().strip() == 'testtinyxml', stdout.getvalue()
            with fakeout() as b:
                rosdep_main(['keys', 'another_catkin_package'] + cmd_extras + ['-i'])
                stdout, stderr = b
                assert stdout.getvalue().strip() == 'catkin', stdout.getvalue()
            with fakeout() as b:
                rosdep_main(['keys', 'multi_dep_type_catkin_package', '-t', 'test', '-t', 'doc'] + cmd_extras)
                stdout, stderr = b
                output_keys = set(stdout.getvalue().split())
                expected_keys = set(['curl', 'epydoc'])
                assert output_keys == expected_keys, stdout.getvalue()
        except SystemExit:
            assert False, 'system exit occurred'
        try:
            rosdep_main(['keys', 'nonexistent'] + cmd_extras)
            assert False, 'system exit should have occurred'
        except SystemExit:
            pass

    def test_search(self):
        sources_cache = get_cache_dir()
        cmd_extras = ['-c', sources_cache]

        try:
            with fakeout() as b:
                rosdep_main(['search', 'curl', '--os=debian:squeeze'] + cmd_extras)
                stdout, stderr = b
                assert 'Closest keys' in stdout.getvalue(), stdout.getvalue()
                assert 'curl' in stdout.getvalue(), stdout.getvalue()
                assert 'Closest packages' not in stdout.getvalue(), stdout.getvalue()
                assert not stderr.getvalue(), stderr.getvalue()
            with fakeout() as b:
                rosdep_main(['search', 'libeigen3-dev', '--os=ubuntu:noble'] + cmd_extras)
                stdout, stderr = b
                assert 'Closest keys' not in stdout.getvalue(), stdout.getvalue()
                assert 'Closest packages' in stdout.getvalue(), stdout.getvalue()
                assert 'eigen:' in stdout.getvalue(), stdout.getvalue()
                assert not stderr.getvalue(), stderr.getvalue()
        except SystemExit:
            assert False, 'system exit occurred'
        try:
            rosdep_main(['search', 'libeigen3-dev', '--os=debian:squeeze'] + cmd_extras)
            assert False, 'system exit should have occurred'
        except SystemExit:
            pass
        try:
            rosdep_main(['search', 'nonexistent'] + cmd_extras + ['-i'])
            assert False, 'system exit should have occurred'
        except SystemExit:
            pass

    @patch('rosdep2.main.install_opener')
    @patch('rosdep2.main.build_opener')
    @patch('rosdep2.main.HTTPBasicAuthHandler')
    @patch('rosdep2.main.ProxyHandler')
    def test_proxy_detection(self, proxy, bah, build, install):
        with patch.dict('os.environ', {'http_proxy': 'something'}, clear=True):
            setup_proxy_opener()
            proxy.assert_called_with({'http': 'something'})
        with patch.dict('os.environ', {'https_proxy': 'somethings'}, clear=True):
            setup_proxy_opener()
            proxy.assert_called_with({'https': 'somethings'})

    def test_invalid_package_message(self):
        with fakeout() as b:
            test_package_dir = os.path.abspath(os.path.join(get_test_dir(), 'main', 'invalid_package_version'))
            with patch('rosdep2.main.sys.exit') as exit_mock:
                rosdep_main(['install', '--from-path', test_package_dir])
                exit_mock.assert_called_with(1)
            stdout, stderr = b
            output = stderr.getvalue().splitlines()
            assert len(output) >= 2
            assert test_package_dir in output[-2]
            assert 'Package version ":{version}" does not follow version conventions' in output[-1]
