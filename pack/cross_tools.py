#!/usr/bin/env python
#
#  cross_tools.py
#

import sys
import os
import shutil
import glob


# This is Debian control file in a skeleton reusable block
# This package can be installed on the sysroot (armhf) and the host (Intel 64)
control_skeleton='''
Maintainer: Albert Casals <skarbat@gmail.com>
Section: others
Package: {pkg_name}
Version: {pkg_version}
Architecture: amd64
Depends: debconf (>= 0.5.00), {pkg_depends}
Priority: optional
Description: {pkg_description}

'''

extra_deps = ''

# These are the packages we are building
# For the moment we are collecting everyting in one single Debian pkg
packages=[

    { 'fileset': '',
      'pkg_name': 'libqt5all-cross-tools',
      'pkg_version': 0,
      'pkg_depends': 'build-essential',
      'pkg_description': 'QT5 Cross compilation tools for Intel x64' }
]


def pack_tools(root_directory, source_directory, qt5_version, tools_directory, cross_compiler, dry_run=False):

    complete_source='{}/{}'.format(root_directory, source_directory)

    # Sanity check
    if not os.path.exists(complete_source):
        print 'error: path not found', complete_source
        sys.exit(1)

    for pkg in packages:

        pkg['pkg_version'] = qt5_version
        pkg['fileset'] = [ tools_directory ]

        # allocate a versioned directory name for the package
        versioned_pkg_name = 'pkgs/{}_{}'.format(pkg['pkg_name'], qt5_version)
        print 'Processing package {}...'.format(versioned_pkg_name)

        # extract the files from the root file system preparing them for packaging
        target_directory = '{}/{}'.format (versioned_pkg_name, source_directory)

        for files in pkg['fileset']:

            # Complete the pathname to the target directory
            last_path = os.path.dirname(files)
            target_files_path='{}/{}'.format(target_directory, last_path)

            print 'Extracting {} into {}...'.format(os.path.join(complete_source, files), target_files_path)
            if not os.path.exists(target_files_path) and not dry_run:
                os.makedirs(target_files_path)

            if not dry_run:
                os.system('cp -rP {} {}'.format(os.path.join(complete_source, files), target_files_path))

        # create the Debian control file for "dpkg-deb" tool to know what to pack
        if not dry_run:
            debian_dir=os.path.join(versioned_pkg_name, 'DEBIAN')
            if not os.path.exists(debian_dir):
                os.makedirs(debian_dir)
            with open(os.path.join(debian_dir, 'control'), 'w') as control_file:
                control_file.writelines(control_skeleton.format(**pkg))

        # Package the cross compiler as well
        cross_target_dir='{}/{}'.format(versioned_pkg_name, cross_compiler)
        print 'Extracting cross compiler {} into {} ...'.format(cross_compiler, cross_target_dir)
        if not dry_run:
            if not os.path.isdir(cross_target_dir):
                os.makedirs(cross_target_dir)

            os.system('cp -rP {}/* {}'.format(cross_compiler, cross_target_dir))
            os.system('find {} -iname \.git -exec rm -rfv \;'.format(cross_compiler))

        # finally call dpkg-deb and generate a debian package
        if not dry_run:
            rc=os.system('dpkg-deb --build {}'.format(versioned_pkg_name))
        else:
            rc=0

        if not rc:
            print 'Package {} created correctly'.format(versioned_pkg_name)
        else:
            print 'WARNING: Error creating package {}'.format(versioned_pkg_name)
