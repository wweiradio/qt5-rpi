#!/usr/bin/env python
#
#  xsysroot
#
#  A small tool that gives sysroot access to multiple ARM Linux images on a i686 host
#
#  The MIT License (MIT)
#
#  Copyright (c) 2015 Albert Casals - albert@mitako.eu
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#
#  The above copyright notice and this permission notice shall be included in
#  all copies or substantial portions of the Software.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#  THE SOFTWARE.

__version__ = 1.914

import os
import sys
import socket
import subprocess
import json
import re

from optparse import OptionParser

default_settings='''
{

  "default" : {
     "description": "pipaOS 3.5 Wheezy",
     "nbdev" : "/dev/nbd5",
     "nbdev_part" : "p2",
     "sysroot" : "/tmp/pipaos",
     "tmp" : "/tmp/pipaos-tmp",
     "backing_image": "~/pipaos-3.5-wheezy-xgui.img.gz",
     "qcow_image": "~/pipaos-3.5-wheezy-xgui.qcow",
     "qcow_size": "4G",
     "display": "1024x768x24 fluxbox"
  }
}
'''

class XSysroot():
    '''
    A class which encapsulates a mount based access to a ARM sysroot image
    '''
    def __init__(self, profile=None, verbose=True):
        self.verbose=verbose
        self.settings_filename='xsysroot.conf'
        self.vnc_start_port=5900
        self.last_profile_cache=os.path.join(os.path.expanduser('~'), '.xsysroot')

        # Filenames in the sysroot to avoid Qemu syscall traps, for cross-CPU native libraries
        self.ld_so_preload='etc/ld.so.preload'
        self.ld_so_preload_backup='{}-disabled'.format(self.ld_so_preload)

        # In order to allow your sysroot access to private network data
        # a custom DNS setting is specified here.
        self.dns_server='8.8.8.8'
        print "profile ", profile
        # choose a settings profile, or set to last used
        if not profile:
            self.profile=self._get_active_profile()
        else:
            # print "profile is ", profile
            self.profile=profile

        self._load_profile()

    def _get_settings_filename(self):
        '''
        Finds the system-wide or private user configuration file
        '''
        settings_system=os.path.join(os.path.expanduser('/etc'), self.settings_filename)
        settings_user=os.path.join(os.path.expanduser('~'), self.settings_filename)
        if os.path.isfile(settings_system):
            # print "settings_system ", settings_system
            return settings_system
        if os.path.isfile(settings_user):
            # print "settings_user", settings_user
            return settings_user

        return None

    def _get_active_profile(self):
        '''
        Returns the profile name you are currently using
        '''
        try:
            with open(self.last_profile_cache, 'r') as f:
                profile,value=f.read().split(':')
                if profile=='last_profile':
                    return value.strip()
        except:
            return 'default'

    def _set_active_profile(self, profilename):
        '''
        Keeps track of the profile you are currently working on
        '''
        with open(self.last_profile_cache, 'w') as f:
            f.write('last_profile: {}\n'.format(profilename))

    def _get_add_mounts(self):
        '''
        Returns a list of dictionaries of additional mount points.
        These optional mounts can be specified using the "add_mounts" key in the
        configuration file, for example:

          "add_mounts" : "p3:/tmp/third p4:/tmp/fourth"

        Each dictionary comes in the form: { 'device': '/dev/nbdXpY', 'mount': '/wherever' }
        If no additional mounts are specified, an empty list is returned.
        '''
        additional_mounts=[]

        if self.settings.has_key('add_mounts'):
            extra_mounts = self.settings['add_mounts'].split(' ')
            for new_mount in extra_mounts:
                new_device, new_mountpoint = new_mount.split(':')
                additional_mounts.append(
                    { 'device' : '{}{}'.format(self.settings['nbdev'], new_device),
                      'mount'  : '{}'.format(new_mountpoint) })

        return additional_mounts

    def _get_virtual_display(self):
        '''
        Returns a display number, requested resolution and Window Manager to allocate a virtual screen.
        '''
        try:
            # Display resolution and window manager to use (e.g. "1024x768x24 fluxbox")
            resolution, win_manager = self.settings['display'].split(' ')

            # The display number will be bound to the same NBDEV device number
            display_number = re.search('(\d+)', self.settings['nbdev']).group()

            return display_number, resolution, win_manager
        except:
            return None, None, None

    def _load_profile(self):
        '''
        Loads your current working profile from the configuration file at /etc,
        your home directory, or embedded in the xsysroot module.
        '''
        self.settings=None
        try:
            filename=self._get_settings_filename()
            if filename:
                with open(filename, 'r') as f:
                    self.settings=json.load(f)[self.profile]
                    print self.settings 
            else:
                filename=__file__
                self.profile='default'
                self.settings=json.loads(default_settings)[self.profile]
                print self.settings 
        except:
            # FIXME: when no self.profile found it does raise exception
            print 'could not load settings - please check Json syntax ({})'.format(filename)
            raise

        self._set_active_profile(self.profile)

        # Expand shell macros, this allows to embed things like (date +%d) and ~ homedir.
        self.settings['sysroot'] = os.popen(
            'echo {}'.format(self.settings['sysroot'])).read().strip('\n')
        self.settings['tmp'] = os.popen(
            'echo {}'.format(self.settings['tmp'])).read().strip('\n')
        self.settings['backing_image'] = os.popen(
            'echo {}'.format(self.settings['backing_image'])).read().strip('\n')
        self.settings['qcow_image'] = os.popen(
            'echo {}'.format(self.settings['qcow_image'])).read().strip('\n')

    def _uncompress_backing_image(self):
        '''
        Uncompress the backing image if necessary, returns the raw image filename
        '''
        if not os.path.isfile(self.settings['backing_image']):
            return None

        file_pathname, extension=os.path.splitext(self.settings['backing_image'])
        if extension in ('.gz', '.zip', '.xz'):
            uncompressed=self.settings['qcow_image'].replace('.qcow', '.img')
            if os.path.isfile(uncompressed):
                print 'Removing uncompressed backing image {}'.format(uncompressed)
                rc=self._run_cmd('rm {}'.format(uncompressed))

            if extension=='.gz':
                uncompress='gunzip -c'
            elif extension=='.zip':
                uncompress='unzip -p'
            elif extension=='xz':
                uncompress='xz --decompress --stdout'
            else:
                return None

            # Uncompress again to get the latest version of the backing image
            print 'Uncompressing image {} into {}'.format(self.settings['backing_image'], uncompressed)
            p=self._run_cmd('{} {} > {}'.format(uncompress, self.settings['backing_image'], uncompressed))
        elif extension in ('.img'):
            # backing file is in raw format, no need to uncompress
            uncompressed=self.settings['backing_image']
        else:
            return None

        return uncompressed

    def _prepare_sysroot(self):
        if not self.is_mounted():
            print 'sysroot not mounted, please mount first with -m'
            return False

        # Copy ARM emulator, setup a default DNS, and remove libcofi.so preload
        print 'Preparing sysroot for chroot to function correctly'

        self._run_cmd('sudo mkdir -p {sysroot}/usr/bin'.format(**self.settings))
        self._run_cmd('sudo cp $(which qemu-arm-static) ' \
                          '{sysroot}/usr/bin'.format(**self.settings))

        self.edfile('/etc/resolv.conf', 'nameserver {}'.format(self.dns_server), verbose=True)
        print 'Preparation done'
        return True

    def _run_cmd(self, command):
        '''
        Runs a command on the host system, returns its error level code
        '''
        rc=os.system(command)
        return os.WEXITSTATUS(rc)

    def _xrun_cmd(self, environment='LC_ALL=C', command='/bin/bash', as_user=None):
        '''
        Runs a command inside the sysroot.
        If the sysroot has a virtual display attached to it,
        the DISPLAY environment variable will be pointing to it.
        '''
        userspec=''
        display, _, _=self._get_virtual_display()
        if display:
            environment='"{}" "{}"'.format(environment, 'DISPLAY=:{}'.format(display))

        if as_user:
            userspec='-c "su - {}"'.format(as_user)

        cmdline='sudo {} chroot {} {} {}'.format(environment, self.settings['sysroot'], command, userspec)
        return self._run_cmd(cmdline)

    def list_profiles(self):
        '''
        Lists all profiles defined in your configuration file
        '''
        print 'Active profile: {} ({})'.format(self.profile, self.settings['description'])
        try:
            print 'Available profiles (* means mounted)'
            filename=self._get_settings_filename()
            if filename:
                with open(filename, 'r') as f:
                    settings=json.load(f)
                    for profile in settings:
                        mounted=' '
                        if self.is_mounted(settings=settings[profile]):
                            mounted = '*'

                        print ' {} {} ({})'.format(mounted, profile, settings[profile]['description'])
            else:
                print 'could not find settings file: {}'.format(self.settings_filename)
        except:
            print 'could not load settings - please check Json syntax'
            raise

    def print_settings(self):
        '''
        Prints all variables defined in the current profile
        '''
        for k,v in self.settings.items():
            
            if k in 'display':
                display, resolution, wmgr=self._get_virtual_display()
                vnc = 'n/a'
                try:
                    # Find out if the VNC server is running on this virtual display
                    x11_vnc_pid = int(os.popen('pgrep -f "[x]11vnc -display :{}"'.format(display)).read())
                    if x11_vnc_pid > 1:
                        vnc='{}:{}'.format(socket.gethostname(), int(display)+self.vnc_start_port)
                except:
                    pass

                v='Display: {}, Resolution: {}, WMgr: {}, VNC: {}'.format(display, resolution, wmgr, vnc)

            print '{:<25}: {}'.format(k,v)

    def print_is_mounted(self):
        '''
        Displays a message to say if the sysroot is mounted
        '''
        ismounted=self.is_mounted()
        print 'sysroot mounted?', ismounted
        return ismounted

    def is_mounted(self, settings=None):
        '''
        Returns True if the current profile sysroot is mounted
        '''
        if not settings:
            settings=self.settings
        rc=self._run_cmd('mountpoint {sysroot} > /dev/null 2>&1'.format(**settings))
        return (rc == 0)

    def status(self):
        '''
        Displays current profile status and variable information
        '''
        print 'Active profile: {} ({})'.format(self.profile, self.settings['description'])
        self.print_is_mounted()
        self.print_settings()
        return True

    def query(self, variable):
        '''
        Queries a variable from your current profile configuration
        Useful for automated scripts - i.e. cat $(xsysroot -q tmp)
        '''
        return self.settings[variable]

    def running(self):
        '''
        Finds and reports any processes currently running on the sysroot
        Returns True if processes were found, False otherwise
        '''
        if not self.is_mounted():
            print 'sysroot not mounted'
            return False

        rc=self._run_cmd('sudo lsof -l {sysroot}'.format(**self.settings))
        return (rc == 0)
        
    def mount(self):
        '''
        Mounts the sysroot image to get ready for use
        '''
        mounted=self.is_mounted()
        if mounted:
            print 'sysroot already mounted'
            return mounted
        elif not os.path.isfile(self.settings['qcow_image']):
                print 'Qcow image not found {qcow_image} - please run "renew"'.format(**self.settings)
                return mounted
        else:
            print 'binding qcow image:', self.settings['qcow_image']
            p=self._run_cmd('mkdir -p {}'.format(self.settings['sysroot']))
            p=self._run_cmd('mkdir -p {}'.format(self.settings['tmp']))

            # Connect the image and mount the "nbdev" root partition
            p=self._run_cmd('sudo qemu-nbd -c {nbdev} {qcow_image}; sync'.format(**self.settings))
            print 'mounting root partition {nbdev}{nbdev_part} -> {sysroot}'.format(**self.settings)
            p=self._run_cmd('sudo mount {nbdev}{nbdev_part} {sysroot}'.format(**self.settings))

            # Disable ld.so.preload from dragging QEMU unsupported syscalls (restored on umount)
            if os.path.isfile('{}/{}'.format(self.query('sysroot'), self.ld_so_preload)):
                self._run_cmd('sudo mv -fv {}/{} {}/{}'.format(
                        self.query('sysroot'), self.ld_so_preload,
                        self.query('sysroot'), self.ld_so_preload_backup))

            # Map linux virtual file systems into the host

            p=self._run_cmd('sudo mount --bind /dev {sysroot}/dev'.format(**self.settings))
            p=self._run_cmd('sudo mount --bind /proc {sysroot}/proc'.format(**self.settings))
            p=self._run_cmd('sudo mount --bind /sys {sysroot}/sys'.format(**self.settings))
            p=self._run_cmd('sudo mount --bind {tmp} {sysroot}/tmp'.format(**self.settings))

            # try to mount the boot partition if specified
            if self.settings.has_key('boot_part') and self.settings.has_key('sysboot'):
                p=self._run_cmd('mkdir -p {}'.format(self.settings['sysboot']))
                print 'mounting boot partition {nbdev}{boot_part} -> {sysboot}'.format(**self.settings)
                rc=self._run_cmd('sudo mount {nbdev}{boot_part} {sysboot}'.format(**self.settings))
                if not rc:
                    # Create a bind mount so the boot partition is accesible from the root
                    self._run_cmd('sudo mount --bind {sysboot} {sysroot}/boot'.format(**self.settings))

            # mount additional partitions if specified
            add_mounts = self._get_add_mounts()
            if len(add_mounts):
                print 'mounting additional partitions'
                for extra_mount in add_mounts:
                    rc=self._run_cmd('mkdir {mount} ; sudo mount {device} {mount}'.format(**extra_mount))
                    print 'mounted {} => {} rc={}'.format(
                        extra_mount['device'], extra_mount['mount'], rc)

            mounted=self.is_mounted()
            print 'Mount done'

        # Start a virtual display server, bound to a tcp endpoint so the sysroot can connect to it
        display_number, resolution, win_manager=self._get_virtual_display()
        if mounted and display_number and resolution and win_manager:

            # Start a Frame Buffer headless X server.
            cmdline='xvfb-run --server-args="-screen 0 {} -shmem" --xauth-protocol="127.0.0.1:{}" ' \
                '--server-num {} {} > /dev/null 2>&1 &'.format(resolution, display_number, display_number, win_manager)

            rc=self._run_cmd(cmdline)
            if rc == 0:
                print 'started display number :{} resolution {} window manager {}'.format(display_number, resolution, win_manager)

            # Start a VNC server attached to the fake X server, on termination it will die when we terminate the headless.
            rc=self._run_cmd('x11vnc -display :{} -forever -shared -bg -N ' \
                                 '-no6 -noipv6 > /dev/null 2>&1'.format(display_number))
            if rc == 0:
                print 'attached a VNC server to display :{} network address {}:{}'.format(
                    display_number, socket.gethostname(), int(display_number)+self.vnc_start_port)

        if not mounted:
            # if problems mounting, tell Qemu to free the image
            p=self._run_cmd('sudo qemu-nbd -d {nbdev}; sync'.format(**self.settings))

        return mounted

    def umount(self):
        '''
        Unmounts the sysroot image and releases associated resources
        '''
        mounted=self.is_mounted()
        if not mounted:
            print 'sysroot is already unmounted'
            return True
        else:
            # sanity check
            if self.running() == True:
                print 'ERROR - there seem to be processes working on this sysroot, umount aborted'
                return False

            # Restore ld.so.preload to its original state (QEMU syscalls safeguard)
            if os.path.isfile('{}/{}'.format(self.query('sysroot'), self.ld_so_preload_backup)):
                self._run_cmd('sudo mv -fv {}/{} {}/{}'.format(
                        self.query('sysroot'), self.ld_so_preload_backup,
                        self.query('sysroot'), self.ld_so_preload))
            
            print 'unbinding {qcow_image}'.format(**self.settings)
            p=self._run_cmd('sudo umount {sysroot}/tmp'.format(**self.settings))
            p=self._run_cmd('sudo umount {sysroot}/sys'.format(**self.settings))
            p=self._run_cmd('sudo umount {sysroot}/proc'.format(**self.settings))
            p=self._run_cmd('sudo umount {sysroot}/dev'.format(**self.settings))

            # try to unmount the boot partition if mounted
            if self.settings.has_key('boot_part') and self.settings.has_key('sysboot'):
                p=self._run_cmd('sudo umount {sysboot}'.format(**self.settings))
                p=self._run_cmd('rmdir {sysboot}'.format(**self.settings))
                p=self._run_cmd('sudo umount {sysroot}/boot'.format(**self.settings))

            p=self._run_cmd('sudo umount {sysroot} ; rmdir {sysroot}'.format(**self.settings))

            # unmount any additional partitions specified
            add_mounts = self._get_add_mounts()
            if len(add_mounts):
                for extra_mount in add_mounts:
                    rc=self._run_cmd('sudo umount {mount} ; rmdir {mount}'.format(**extra_mount))

            p=self._run_cmd('sudo qemu-nbd -d {nbdev}; sync'.format(**self.settings))
            mounted=self.is_mounted()

        # Stop the virtual display
        display_number, resolution, win_manager=self._get_virtual_display()
        if not mounted and display_number:
            print 'Disconnecting Display number {} and window manager {}'.format(display_number, win_manager)
            self._run_cmd('pkill -f "Xvfb :{}"'.format(display_number))

        print 'Unmount done'
        return (mounted == False)

    def renew(self):
        '''
        Recreates the sysroot from scratch unfolding the original backing image
        '''
        if self.is_mounted():
            print 'sysroot is mounted, please unmount first'
            return False

        # stop now if the backing image is not available
        if not os.path.isfile('{backing_image}'.format(**self.settings)):
            print self.settings
            print 'Could not find backing image - aborting'
            return False

        if os.path.isfile('{qcow_image}'.format(**self.settings)):
            print 'Removing qcow image {qcow_image}'.format(**self.settings)
            rc=self._run_cmd('rm {qcow_image}'.format(**self.settings))

        # Get the original backing image, which means uncompress it if necessary
        uncompressed=self._uncompress_backing_image()
        if not uncompressed:
            print 'Backing image not found or unsupported format: {}'.format(self.settings['backing_image'])
            return False

        # Qcow image size can match the original backing image,
        # or be forced to be larger which allows to expand the filesystem (--expand option)
        # A smaller value will not work and the mount will likely fail to proceed.
        qcow_size=''
        try:
            qcow_size=self.settings['qcow_size']
            print 'Creating qcow image {qcow_image} of new forced size {qcow_size}'.format(**self.settings)
        except:
            print 'Creating qcow image {qcow_image} of original size'.format(**self.settings)

        p=self._run_cmd('qemu-img create -f qcow2 -b {} {} {}'.format(
                uncompressed, self.settings['qcow_image'], qcow_size))

        if self.mount():
            # Prepare image settings to chroot and access network
            self._prepare_sysroot()
            print 'Renew done'
            return True
        else:
            print 'Error renewing sysroot'
            return False

    def expand(self):
        '''
        Expands the last ext2/ext4/ext4 partition to fit the image size
        as specified by the "qcow_size" setting. The image has to be unmounted.
        Use this function with care, i.e. assume "qcow_image" is volatile.
        '''
        expanded=False
        modified=False

        if self.is_mounted():
            print 'sysroot is mounted, please unmount before expanding'
            return False

        # connect the image to a disk device
        disk_device='{nbdev}'.format(**self.settings)
        part_device='{nbdev}{nbdev_part}'.format(**self.settings)
        print 'Connecting image {qcow_image} to find and expand last partition'.format(**self.settings)
        rc=self._run_cmd('sudo qemu-nbd -c {nbdev} {qcow_image}; sync'.format(**self.settings))
        qcow_image = self.settings['qcow_image']
        if qcow_image[-1]=='G':
            qcow_image = int(qcow_image[0:-1])*1024
        elif qcow_image[-1]=='K':
            qcow_image = int(qcow_image[0:-1]) / 1024.0

        if rc:
            print 'error connecting image rc={}'.format(rc)
            return False

        def print_partition_info():
            try:
                # parse details reported by "parted"
                info_partition=os.popen('sudo parted --script --machine {nbdev} print'.format(**self.settings)).readlines()
                part_num, part_start, part_end, part_size, part_type, unknown1, unknown2=info_partition[len(info_partition)-1].strip().split(':')
                print 'Partition number: {} start: {} end: {} size: {} type: {}'.format(part_num, part_start, part_end, part_size, part_type)
                return part_num, part_start, part_end, part_size, part_type
            except:
                return None, None, None, None, None

        # find last partition details and recreate it to fit image size extension
        part_num, part_start, part_end, part_size, part_type=print_partition_info()
        if not part_num:
            print 'Could not find partition details, aborting'
        else:
            if part_type not in ('ext2', 'ext3', 'ext4'):
                print 'Could not expand partition number {} of type {}'.format(part_num, part_type)
            else:
                part_size = int(part_size[0:-1])
                if abs(part_size - qcow_image) < 1:
                    expanded = True
                    print "OK, got here expanded."
                    return expanded

                rc=self._run_cmd('sudo parted --script {} rm {}'.format(disk_device, part_num))
                modified=True
                if rc:
                    print 'Error removing partition number {}'.format(part_num)
                else:
                    rc=self._run_cmd('sudo parted --script {} mkpart primary {} {} 100%'.format(disk_device, part_type, part_start))
                    if rc:
                        print 'Error creating new partition at offset {}'.format(part_start)
                    else:
                        rc=self._run_cmd('sudo e2fsck -p -f {}; sudo resize2fs {}; sync'.format(part_device, part_device))
                        if rc:
                            print 'Error checking and resizing new partition {}'.format(part_device)
                        else:
                            print 'Image partition expanded successfully, new layout:'
                            print_partition_info()
                            expanded=True
    
        # report results and disconnect image from disk device
        if not expanded:
            print 'Errors were found and the partition was not expanded'
            if modified:
                print 'The image integrity might be compromised - you should run "renew"'

        rc=self._run_cmd('sudo qemu-nbd -d {nbdev}; sync'.format(**self.settings))
        return expanded

    def zerofree(self, partition='all', verbose=True):
        '''
        Fills up a partition free space with zeroes. Increases final image compression ratio.
        partition is the NBDEV device name, by default it will zero sysroot partition (nbdev_part variable).
        The image needs to be unmounted for this function to work.
        '''
        success=False

        if self.is_mounted():
            print 'sysroot is mounted - aborting'
        elif partition=='all':
            rc=self.zerofree(self.query('nbdev_part'))
            success = (rc==0)
        elif not re.match('(p\d+)$', partition):
            print 'Partition name not recognized, must be p1, p2, .. {}'.format(partition)
        else:
            print 'connecting image {} to zerofree partition {}'.format(self.query('qcow_image'), partition)
            rc_connect=self._run_cmd('sudo qemu-nbd -c {nbdev} {qcow_image}; sync'.format(**self.settings))
            if rc_connect:
                print 'error connecting image'
            else:
                zero_device='{}{}'.format(self.query('nbdev'), partition)
                print 'zero free', zero_device
                cmdline='sudo zerofree {}'.format(zero_device)
                if verbose:
                    cmdline += ' -v'
                rc=self._run_cmd(cmdline)

            print 'disconnecting image'
            rc_disconnect=self._run_cmd('sudo qemu-nbd -d {nbdev}; sync'.format(**self.settings))
            success = (rc_connect==0)

        return success

    def execute(self, command, verbose=True, pipes=False, as_user=None):
        '''
        Executes a command inside the sysroot.
        Returns the program errorlevel, -1 if failure.

        Set pipes to True if you are chaining commands through a pipe,
        or passing environment variables as in "MYVAR=1; do-whatever"

        as_user will execute the command as a different account in the sysroot via su.
        '''
        if not self.is_mounted():
            print 'sysroot not mounted - aborting'
            return -1
        else:
            # If the command uses pipes, embrace it inside bash
            if pipes:
                command = "/bin/bash -c '{}'".format(command)

            if as_user:
                command = 'su -l {} -c "{}"'.format(as_user, command)

            if verbose:
                print 'sysroot executing: {}'.format(command)

            return self._xrun_cmd(command=command)

    def edfile(self, filename, literal, append=False, verbose=True):
        '''
        Dumps a literal into a file, appending at the end if specified.
        If you need to pass quotation marks, make sure to escape them.
        '''
        redirection = '>'
        if append:
            redirection += '>'

        return self.execute('/bin/bash -c "echo \'{}\' {} {}"'.format(
                literal, redirection, filename), verbose)

    def screenshot(self, filename='screenshot.png'):
        '''
        Takes a screnshot of the current sysroot virtual display
        '''
        success=False

        if not self.is_mounted():
            print 'sysroot not mounted - aborting'
            return success

        display, _, _=self._get_virtual_display()
        if display:
            rc=self._run_cmd('import -display :{} -window root {}'.format(display, filename))
            if rc:
                print 'Error taking a screenshot from display {}'.format(display)
            else:
                success=True
        else:
            print 'This sysroot does not have a virtual display'

        return success

    def jail(self):
        '''
        Protects harmful commands in the sysroot (reboot, shutdown)
        and provides a blind sudo without password prompts.
        You may find this option useful for testing software.
        '''
        if not self.is_mounted():
            print 'sysroot not mounted - please mount first with -m'
            return False

        # disable reboot tools
        self.execute('ln -sfv $(which true) $(which reboot)', pipes=True, verbose=False)
        self.execute('ln -sfv $(which true) $(which poweroff)', pipes=True, verbose=False)
        self.execute('ln -sfv $(which true) $(which shutdown)', pipes=True, verbose=False)
        self.execute('ln -sfv $(which true) $(which halt)', pipes=True, verbose=False)
 
        # blind sudo - make sure your user belongs to "sudo" group
        self.edfile('/etc/sudoers', '%sudo   ALL=NOPASSWD: ALL', append=True)

        # fake hostname to match the host system
        import socket
        host_hostname=socket.gethostname()
        self.edfile('/etc/hosts', '127.0.0.1   {}'.format(host_hostname), append=True)

        return True

    def chroot(self, username=None):
        '''
        Starts an interactive shell into the ARM sysroot as the superuser.
        Returns chrooted command termination error level.
        '''
        if not self.is_mounted():
            print 'sysroot not mounted - please mount first with -m'
            return -1
        else:
            print 'Starting sysroot shell into: {sysroot}'.format(**self.settings),
            if username:
                print 'as user', username
            else:
                print 'as the superuser'

            return self._xrun_cmd(as_user=username)

    def depends(self, repo_dir='.'):
        '''
        Parses a Debian package control file, and install build-depends in the sysroot
        '''
        success=False
        control_file=os.path.join(repo_dir, 'debian/control')
        if not self.is_mounted():
            print 'sysroot not mounted - aborting'
        else:
            pkgs_install=''
            print 'Checking Build-Dependencies at {}'.format(control_file)
            output = subprocess.Popen(['dpkg-checkbuilddeps', control_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            rc=output.wait()
            if rc == 1:
                # Dependencies need to be installed:
                # Parsing the output of dpkg-checkbuilddeps
                for item in output.stderr.read().split()[4:]:
                    if item.find('(') == -1 and item.find(')') == -1:
                        pkgs_install += '{} '.format(item)
                        
                print 'Installing packages in the sysroot:', pkgs_install
                rc=self._xrun_cmd(command='apt-get --no-install-recommends -y install {}'.format(pkgs_install))

                print 'apt-get completed with rc={}'.format(rc)
                success=(rc==0)
            elif rc == 2:
                print 'dpkg-checkbuilddeps returned with rc={} - no debian control file found'.format(rc)
            else:
                print 'dpkg-checkbuilddeps failed with unknown rc={}'.format(rc)
                
        return success

    def build(self, repo_dir='.', debuild_cmd='echo "y" | debuild --preserve-envvar PATH -us -uc -d -aarmhf'):
        debian_dir=os.path.abspath(os.path.join(repo_dir, 'debian'))
        pkg_dir=os.path.abspath(repo_dir + '/../')
        if not os.path.exists(debian_dir):
            print 'cannot find debian package directory at: {}'.format(debian_dir)
            return False

        # Resolve build dependencies
        self.depends(repo_dir=repo_dir)

        print 'building package for repo', repo_dir
        rc=os.system('cd {} && {}'.format(repo_dir, debuild_cmd))
        if rc:
            print 'ERROR - failure building package for repo {}'.format(repo_dir)
            return False
        else:
            print 'OK - Debian package built successfuly'
            return True


def is_os_platform_supported():
    '''
    Returns True if your local system processor and
    operating system is supported by xsysroot.
    '''
    local_os = os.popen('uname -s').read().strip()
    if not local_os == 'Linux':
        return False

    local_machine = os.popen('uname -m').read().strip()
    if local_machine not in ('i686', 'armv7l', 'armv6', 'armv6l', 'x86_64'):
        return False

    return True


def check_system_tools():
    '''
    Checks wether you have the necessary tools to fully utilize xsysroot
    Returns True if you can rock & roll, False otherwise
    '''
    core_tools_ready = 0
    min_core_tools = 3

    if not is_os_platform_supported():
        print 'Sorry, your local system is not supported,'
        print 'Xsysroot runs on i386 and ARM based Linux systems.'
        return False

    # check if the NBD kernel modules is loaded
    rc=os.system('lsmod | grep nbd > /dev/null 2>&1')
    if (rc):
        print 'The NBD kernel module is not loaded'
        print ' please add to /etc/modules: "nbd nbds_max=64 max_part=4"'
    else:
        core_tools_ready += 1

    # check for password-less sudo
    rc = os.system('sudo -n -k whoami > /dev/null 2>&1')
    if rc:
        print 'Error: You do not seem to have sudo password-less permissions, please run visudo to fix'
    else:
        core_tools_ready += 1

    # check for qemu tools
    rc = os.system('which qemu-arm-static qemu-img qemu-nbd  > /dev/null 2>&1')
    if rc:
        print 'Error: please install qemu-user-static and qemu-utils packages'
    else:
        core_tools_ready += 1
    
    # check for bin format support
    rc = os.system('sudo -n -k which update-binfmts  > /dev/null 2>&1')
    if rc:
        print 'Error: please install binfmt-support package'

    # The debian packge build-depends tool
    rc = os.system('which dpkg-checkbuilddeps > /dev/null 2>&1')
    if rc:
        print 'Warning: dpkg-checkbuilddeps cannot be found, --depends will not work'

    # The debian packge build-depends tool
    rc = os.system('which dpkg-checkbuilddeps > /dev/null 2>&1')
    if rc:
        print 'Warning: debuild not found, please install devscripts package if you want to --build'

    # The fake X server to provide a virtual screen to sysroots
    rc = os.system('which Xvfb > /dev/null 2>&1')
    if rc:
        print 'Warning: Xvfb is not available, virtual displays will not be available'

    # The VNC server will allow to connect to the xsysroot virtual display remotely
    rc = os.system('which x11vnc > /dev/null 2>&1')
    if rc:
        print 'Warning: X11VNC is not available, install it to connect remotely to virtual displays'

    # Imagemagick import for taking screenshots
    rc = os.system('which import > /dev/null 2>&1')
    if rc:
        print 'Warning: tool "import" not found, screenshots will not be available (you need ImageMagick)'

    # Parted tool to generate raw images
    rc1 = os.system('sudo -n -k which parted > /dev/null 2>&1')
    rc2 = os.system('sudo -n -k which resize2fs > /dev/null 2>&1')
    rc3 = os.system('sudo -n -k which e2fsck > /dev/null 2>&1')
    if rc1 or rc2 or rc3:
        print 'Warning: tools "parted", "resize2fs" or "e2fsck" not found, --geometry option will not be available'

    # If minimal core tools are available, xsysroot is ready to work
    if core_tools_ready >= min_core_tools:
        return True

    return False


def create_debian_skeleton(prj_directory):
    '''
    Creates an empty Debian skeleton folder to generate a package with "debuild"
    '''

    changelog='''xsysroot (1.0-1) unstable; urgency=low

  * xsysroot initial version

 -- Albert Casals <albert@mitako.eu>  Fri, 20 Jun 2015 00:00:00 +0100
'''

    control='''Source: xsysroot
Maintainer: Albert Casals <albert@mitako.eu>
Section: shells
Priority: optional
Standards-Version: 3.9.4
Build-Depends: debhelper (>=9.0.0)

Package: xsysroot
Architecture: all
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: A small tool that gives sysroot access to multiple ARM Linux images on a i686 host

'''

    rules='''
#!/usr/bin/make -f

%:
\tdh $@
'''

    if not os.path.exists(prj_directory):
        print 'error - cannot access directory: {}'.format(prj_directory)
        return False

    debian_directory=os.path.join(prj_directory, 'debian')
    if os.path.exists(debian_directory):
        print 'error - debian directory already exists: {}'.format(debian_directory)
        return False

    rc=os.system('mkdir -p {}'.format(debian_directory))
    if rc:
        print 'error - could not create directory: {}'.format(debian_directory)

    with open(os.path.join(debian_directory, 'changelog'), 'w') as f:
        f.writelines(changelog)

    rc=os.system('echo "9" > {}'.format(os.path.join(debian_directory, 'compat')))
    rc=os.system('echo "./xsysroot /usr/bin" > {}'.format(os.path.join(debian_directory, 'files')))

    with open(os.path.join(debian_directory, 'control'), 'w') as f:
        f.writelines(control)

    with open(os.path.join(debian_directory, 'rules'), 'w') as f:
        f.writelines(rules)

    print 'Debian skeleton package created at: {}'.format(debian_directory)
    return True
    
def create_image(geometry, nbdev='/dev/nbd0'):
    '''
    Builds an empty image file with partitions of given size and file system types
    Geometry specifies the image layout in the form "imagefile.img fstype1:size_mb fstype2:size_mb"
    Currently supported file system types are "fat" and "ext2" to "ext4".
    '''

    image_size=0
    part_offset=0
    partitions=[]

    def safe_exec(cmdline, silent=True):
        '''
        Executes a command making sure it succeds
        '''
        try:
            if silent:
                cmdline=cmdline + ' >/dev/null 2>&1'
            rc=os.system(cmdline)
            assert(rc==0)
        except Exception as err:
            raise IOError('Error executing step: {}'.format(cmdline))

    # collect geometry details
    details=geometry.split(' ')
    filename=details[0]

    if os.path.isfile(filename):
        print 'image file already exists: {}'.format(filename)
        return False

    for partnum, partition in enumerate(details[1:]):
        partype, partsize=partition.split(':')
        partitions.append({ 'partnum' : partnum, 'partype' : partype, 'partsize': partsize })
        image_size += int(partsize)

    print 'creating {}MB image file {}...'.format(image_size, filename)
    rc=safe_exec('dd if=/dev/zero of={} bs=1MB count={}'.format(filename, image_size))
    rc=safe_exec('sudo parted --script {} mklabel msdos'.format(filename))

    for part in partitions:
        print ' partition {} type {} size {}MB'.format(part['partnum'], part['partype'], part['partsize'])
        if part['partnum'] == 0:
            # this is the first partition
            rc=safe_exec('sudo parted --script {} mkpart primary {} -- 1 {}MB'.format(filename, part['partype'], part['partsize']))
        elif part['partnum'] < len(partitions) -1:
            # intermediate partition
            disk_offset=part_offset + int(part['partsize'])
            rc=safe_exec('sudo parted --script {} mkpart primary {} {}MB {}MB'.format(filename, part['partype'], part_offset, disk_offset))
        elif part['partnum'] == len(partitions) -1:
            # this is the last partition
            rc=safe_exec('sudo parted --script {} mkpart primary {} {}MB 100%'.format(filename, part['partype'], part_offset))

        # keep track of the current partition offset in MB
        part_offset += int(part['partsize']) + 1

    # Map the image through a disk device so we can format partitions
    print 'formatting partitions...',
    sys.stdout.flush()
    rc=safe_exec('sudo /usr/bin/qemu-nbd -d {}'.format(nbdev))
    rc=safe_exec('sudo /usr/bin/qemu-nbd -f raw -c {} {}'.format(nbdev, filename))
    for part in partitions:
        if part['partype'].startswith('fat'):
            rc=safe_exec('sudo mkfs.vfat -n "xsysroot" -v -F 16 {}p{}'.format(nbdev, part['partnum'] + 1))
        elif part['partype'].startswith('ext'):
            rc=safe_exec('sudo mkfs.{} -q -O ^huge_file {}p{}'.format(part['partype'], nbdev, part['partnum'] + 1))

    rc=safe_exec('sudo /usr/bin/qemu-nbd -d {}'.format(nbdev))
    print 'done!'
    return True

def report_integrity():
    '''
    Steps through each xsysroot profile and displays information
    for each backing and qcow image (storage used, broken links)
    Returns False if any backing image cannot be found.
    '''
    xsys=XSysroot()
    total_backing_size = total_qcow_size = 0
    mib_units = (1024 * 1024)
    broken_backings = 0

    # load all profiles
    filename=xsys._get_settings_filename()
    if filename:
        with open(filename, 'r') as f:
            all_profiles=json.load(f)

    print 'xsysroot image storage report\n'
    for profile in all_profiles:

        description = all_profiles[profile]['description']
        backing = all_profiles[profile]['backing_image']
        qcow = all_profiles[profile]['qcow_image']

        # expand shell tokens in the image filenames
        backing = os.popen('echo {}'.format(backing)).read().strip('\n')
        qcow = os.popen('echo {}'.format(qcow)).read().strip('\n')

        status_backing = 'NOT FOUND'
        status_qcow    = 'UNRENEWED'
        if os.path.isfile(backing):
            bsize = os.path.getsize(backing) / mib_units
            status_backing = '{} MiB'.format(bsize)
            total_backing_size += bsize
        else:
            broken_backings += 1

        if os.path.isfile(qcow):
            qsize =os.path.getsize(qcow) / mib_units
            status_qcow = '{} MiB'.format(qsize)
            total_qcow_size += qsize

        print '{} ({})\n backing image => {} => {}\n    qcow image => {} => {}'.format( \
            profile, description, status_backing, backing, status_qcow, qcow)

    print '\nBacking image storage: {} MiB'.format(total_backing_size)
    print 'Qcow image storage: {} MiB'.format(total_qcow_size)
    print 'Total disk space: {} MiB'.format(total_backing_size + total_qcow_size)
    print 'Broken backing image links: {}'.format(broken_backings)

    return (broken_backings == 0)

def upgrade_xsysroot():
    '''
    Function to automatically upgrade your installed xsysroot binary.
    Contacts github to fetch the latest version and replaces itself.
    Additionally, a symlink will be created to import xsysroot from Python.
    You most likely need root permissions if installed in a system directory.
    '''

    success=False
    xsysroot_github_url='raw.githubusercontent.com'
    xsysroot_file_url='/skarbat/xsysroot/master/xsysroot'

    import httplib
    import re

    print 'Contacting github...'
    connection = httplib.HTTPSConnection(xsysroot_github_url)
    connection.request('GET', xsysroot_file_url)
    response=connection.getresponse()

    if response.status==200 and response.reason=='OK':
        # Connected to github, fetch the code and close session
        xsysroot_code=response.read()
        response.close()

        # Compare versions
        import re
        find_version = re.search(r'__version__[\s]=[\s](.*)', xsysroot_code)
        if not find_version:
            print 'Could not find latest xsysroot version on Github'
        else:
            version_at_github=float(find_version.group(1))
            if version_at_github > float(__version__):

                local_xsysroot_filename=os.popen('which xsysroot').read().strip()

                # Upgrade by replacing the xsysroot binary directly
                with open(local_xsysroot_filename, 'w') as f_xsysroot:
                    f_xsysroot.write(xsysroot_code)

                print 'Upgraded your {} version from {} to {}'.format(
                    local_xsysroot_filename, __version__, version_at_github)

                success=True

            elif version_at_github < float(__version__):
                print 'Your local version ({}) is higher than that on github ({}), did you fork it?'.format(
                    __version__, version_at_github)
            else:
                print 'xsysroot is already the latest version: {}'.format(version_at_github)
                success=True

    else:
        print 'Could not fetch xsysroot: {}{} - {} {}'.format(
            xsysroot_github_url, xsysroot_file_url, response.status, response.reason)

    # Pythonize xsysroot (harmless if we can't do it)
    try:
        import xsysroot
    except ImportError:
        try:
            # Find the first path to Python system modules
            syspath=next(i for i in sys.path if i.find('python') != -1)
            print 'Creating python symlink to xsysroot at:', syspath
            os.system('ln -s $(which xsysroot) {}'.format(
                os.path.join(syspath, 'xsysroot.py')))
        except:
            print 'Could not create python symlink to xsysroot'

    return success



if __name__ == '__main__':

    success=False

    parser = OptionParser()

    parser.add_option("-v", "--verbose", action="store_true", dest="verbose", default=False,
                      help="print more progress information")

    parser.add_option("-V", "--version", action="store_true", dest="version", default=False,
                      help="print xsysroot version and exit")

    parser.add_option("-t", "--tools", action="store_true", dest="tools", default=False,
                      help="performs a basic test to see if system tools are ready")

    parser.add_option("-p", "--profile", dest="profile", metavar="PROFILE", default=None,
                      help="switch to a different sysroot profile")

    parser.add_option("-l", "--list", action="store_true", dest="list",
                      help="list all available sysroot profiles")

    parser.add_option("-s", "--status", dest="status", action="store_true",
                      help='display settings and mount status')

    parser.add_option("-q", "--query", dest="query", metavar="VAR",
                      help='query a profile variable name')

    parser.add_option("-n", "--running", dest="running", action="store_true",
                      help='display any processes currently running on the sysroot')

    parser.add_option("-i", "--is-mounted", dest="ismounted", action="store_true",
                      help='returns wether sysroot is mounted')

    parser.add_option("-r", "--renew", dest="renew", action="store_true",
                      help='rebuilds sysroot from scratch - QCOW DATA WILL BE LOST')

    parser.add_option("-e", "--expand", dest="expand", action="store_true",
                      help='expands sysroot partition to fit image size, preserving data (must be ext2/ext3/ext4)')

    parser.add_option("-m", "--mount", dest="mount", action="store_true",
                      help='mount the current qcow image')

    parser.add_option("-u", "--umount", dest="umount", action="store_true",
                      help='unmount the current qcow image')

    parser.add_option("-j", "--jail", dest="jail", action="store_true",
                      help='Protect xsysroot against reboot harm on the host, give blind sudo')

    parser.add_option("-c", "--chroot", dest="chroot", action="store_true",
                      help='jumps into an interactive ARM shell as the superuser, or specify alternate username')

    parser.add_option("-x", "--execute", dest="execute", metavar="CMD",
                      help='executes a command in the sysroot, "@user command" to switch to account')

    parser.add_option("-o", "--screenshot", dest="screenshot", metavar="IMAGE_FILE",
                      help='take a screenshot of the virtual display (extension determines format)')

    parser.add_option("-d", "--depends", dest="depends", action="store_true",
                      help='installs Debian "Build-Depends" on the sysroot')

    parser.add_option("-b", "--build", dest="build", action="store_true",
                      help='performs a Debian "debuild" on the host (cross build a package)')

    parser.add_option("-k", "--skeleton", dest="skeleton", metavar="DIRECTORY", default=None,
                      help='gives you a Debian package control directory skeleton')

    parser.add_option("-g", "--geometry", dest="image", metavar="GEOMETRY", default=None,
                      help='create and partition new image using geometry in MB (e.g. "myimage.img fat32:40 ext3:200"')

    parser.add_option("-z", "--zerofree", dest="zerofree", action="store_true",
                      help='fill all partitions free space with zeroes to increase compression ratio"')

    parser.add_option("-I", "--integrity", dest="integrity", action="store_true",
                      help='A report of disk images used by xsysroot profiles')

    parser.add_option("-U", "--upgrade", dest="upgrade", action="store_true",
                      help='Upgrade to the latest version of xsysroot')

    (options, args) = parser.parse_args()

    # instantiate the class that does the big job
    xsys=XSysroot(profile=options.profile, verbose=options.verbose)

    # if -V, print version and exit
    if options.version:
        print 'xsysroot version: {}'.format(__version__)
        sys.exit(0)

    # if -U, upgrade xsysroot and quit
    if options.upgrade:
        if upgrade_xsysroot():
            sys.exit(0)

        sys.exit(1)

    # if -l, list profiles and quit
    if options.list:
        xsys.list_profiles()
        sys.exit(0)

    # do the task requested
    if options.status:
        success=xsys.status()
    elif options.query:
        value=xsys.query(options.query)
        if value:
            print value
            success=True
    elif options.integrity:
        success=report_integrity()
    elif options.running:
        success=xsys.running()
    elif options.ismounted:
        is_mounted=xsys.print_is_mounted()
        sys.exit(is_mounted == True)
    elif options.renew:
        success=xsys.renew()
    elif options.expand:
        success=xsys.expand()
    elif options.mount:
        success=xsys.mount()
    elif options.umount:
        success=xsys.umount()
    elif options.jail:
        success=xsys.jail()
    elif options.chroot:
        username=None
        if args:
            username=args[0]
        rc=xsys.chroot(username=username)
        sys.exit(rc)
    elif options.execute:

        command=options.execute
        username=None
        if command[0] == '@':
            username=command.split(' ')[0][1:]
            command=command[len(username) + 2:]

        rc=xsys.execute(command, as_user=username)
        sys.exit(rc)
    elif options.screenshot:
        success=xsys.screenshot(options.screenshot)
    elif options.depends:
        success=xsys.depends()
    elif options.build:
        success=xsys.build()
    elif options.skeleton:
        success=create_debian_skeleton(options.skeleton)
    elif options.image:
        success=create_image(options.image)
    elif options.zerofree:
        success=xsys.zerofree()
    elif options.tools:
        if not check_system_tools():
            print 'xsysroot will not run.'
            sys.exit(1)
        else:
            print 'Core system tools seem to be ready, xsysroot should work fine'
            sys.exit(0)
    else:
        print 'please tell me what to do, -h to see available commands'
        sys.exit(1)

    sys.exit(success==False)
