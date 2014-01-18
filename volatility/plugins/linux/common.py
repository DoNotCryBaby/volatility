# Volatility
# Copyright (C) 2007-2013 Volatility Foundation
#
# This file is part of Volatility.
#
# Volatility is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# Volatility is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Volatility.  If not, see <http://www.gnu.org/licenses/>.
#

"""
@author:       Andrew Case
@license:      GNU General Public License 2.0
@contact:      atcuno@gmail.com
@organization: 
"""

import volatility.commands as commands
import volatility.utils as utils
import volatility.debug as debug
import volatility.obj as obj

from bisect import bisect_right

MAX_STRING_LENGTH = 256

nsecs_per = 1000000000

class vol_timespec:

    def __init__(self, secs, nsecs):
        self.tv_sec  = secs
        self.tv_nsec = nsecs

def set_plugin_members(obj_ref):
    obj_ref.addr_space = utils.load_as(obj_ref._config)

    if not obj_ref.is_valid_profile(obj_ref.addr_space.profile):
        debug.error("This command does not support the selected profile.")

class AbstractLinuxCommand(commands.Command):
    def __init__(self, *args, **kwargs):
        self.addr_space = None
        self.known_addrs = {}
        commands.Command.__init__(self, *args, **kwargs)

    @property
    def profile(self):
        if self.addr_space:
            return self.addr_space.profile
        return None

    def execute(self, *args, **kwargs):
        commands.Command.execute(self, *args, **kwargs)

    @staticmethod
    def is_valid_profile(profile):
        return profile.metadata.get('os', 'Unknown').lower() == 'linux'

    def is_known_address(self, addr, modules):

        text = self.profile.get_symbol("_text")
        etext = self.profile.get_symbol("_etext")

        return (self.addr_space.address_compare(addr, text) != -1 and self.addr_space.address_compare(addr, etext) == -1) or self.address_in_module(addr, modules)

    def address_in_module(self, addr, modules):
    
        for (_, start, end) in modules:
            if self.addr_space.address_compare(addr, start) != -1 and self.addr_space.address_compare(addr, end) == -1:
                return True
    
        return False

    def verify_ops(self, ops, op_members, modules):

        for check in op_members:
            addr = ops.m(check)

            if addr and addr != 0:

                if addr in self.known_addrs:
                    known = self.known_addrs[addr]
                else:
                    known = self.is_known_address(addr, modules)
                    self.known_addrs[addr] = known
                
                if known == 0:
                    yield (check, addr)

class AbstractLinuxIntelCommand(AbstractLinuxCommand):
    @staticmethod
    def is_valid_profile(profile):
        return AbstractLinuxCommand.is_valid_profile(profile) \
        and (profile.metadata.get('arch').lower() == 'x86' \
        or profile.metadata.get('arch').lower() == 'x64')

class AbstractLinuxARMCommand(AbstractLinuxCommand):
    @staticmethod
    def is_valid_profile(profile):
        return AbstractLinuxCommand.is_valid_profile(profile) \
        and (profile.metadata.get('arch').lower() == 'arm')                   
 
def walk_internal_list(struct_name, list_member, list_start, addr_space = None):
    if not addr_space:
        addr_space = list_start.obj_vm

    while list_start:
        list_struct = obj.Object(struct_name, vm = addr_space, offset = list_start.v())
        yield list_struct
        list_start = getattr(list_struct, list_member)

# based on __d_path
def do_get_path(rdentry, rmnt, dentry, vfsmnt):
    ret_path = []

    inode = dentry.d_inode

    if not rdentry.is_valid() or not dentry.is_valid():
        return []

    while (dentry != rdentry or vfsmnt != rmnt) and dentry.d_name.name.is_valid():

        dname = dentry.d_name.name.dereference_as("String", length = MAX_STRING_LENGTH)

        ret_path.append(dname.strip('/'))

        if dentry == vfsmnt.mnt_root or dentry == dentry.d_parent:
            if vfsmnt.mnt_parent == vfsmnt.v():
                break
            dentry = vfsmnt.mnt_mountpoint
            vfsmnt = vfsmnt.mnt_parent
            continue

        parent = dentry.d_parent
        dentry = parent

    ret_path.reverse()

    if ret_path == []:
        return []

    ret_val = '/'.join([str(p) for p in ret_path if p != ""])

    if ret_val.startswith(("socket:", "pipe:")):
        if ret_val.find("]") == -1:
            ret_val = ret_val[:-1] + ":[{0}]".format(inode.i_ino)
        else:
            ret_val = ret_val.replace("/", "")

    elif ret_val != "inotify":
        ret_val = '/' + ret_val

    return ret_val

def get_new_sock_pipe_path(dentry):
    sym = dentry.obj_vm.profile.get_symbol_by_address("kernel", dentry.d_op.d_dname)
    
    if sym:
        if sym == "sockfs_dname":
            pre_name = "socket"    
    
        elif sym == "anon_inodefs_dname":
            pre_name = "anon_inode"

        elif sym == "pipefs_dname":
            pre_name = "pipe"
        else:
            print "no handler for %s" % sym
            pre_name = "<BAD>"

        ret = "%s:[%d]" % (pre_name, dentry.d_inode.i_ino)

    else:
        ret = "<BAD d_dname pointer>"

    return ret

def get_path(task, filp):
    rdentry = task.fs.get_root_dentry()
    rmnt = task.fs.get_root_mnt()
    dentry = filp.dentry
    vfsmnt = filp.vfsmnt

    if dentry.d_op and dentry.d_op.d_dname:
        ret = get_new_sock_pipe_path(filp.dentry)
    else:
        ret = do_get_path(rdentry, rmnt, dentry, vfsmnt)

    return ret

'''
class LinuxStringsMethods:
    def get_process_list(self):
        import volatility.plugins.linux.pslist as linux_pslist

        addr_space = utils.load_as(self._config)
       
        tasks = linux_pslist.linux_pslist.calculate(self)

        try:
            if self._config.PIDS is not None:
                pidlist = [int(p) for p in self._config.PIDS.split(',')]
                tasks = [t for t in tasks if int(t.pid) in pidlist]
        except (ValueError, TypeError):
            # TODO: We should probably print a non-fatal warning here
            pass

        return addr_space, tasks

    def loaded_kernel_modules(self, addr_space):    
        import volatility.plugins.linux.lsmod as linux_lsmod
        
        mods = dict((addr_space.address_mask(mod[0].module_core), mod[0]) for mod in linux_lsmod.linux_lsmod(self._config).calculate())
        mod_addrs = sorted(mods.keys())
         
        return (mods, mod_addrs)

    def find_module(self, modlist, mod_addrs, addr_space, addr):
        pos = bisect_right(mod_addrs, addr) - 1
        if pos == -1:
            return None
        mod = modlist[mod_addrs[pos]]

        if (mod.obj_vm.address_compare(addr, mod.module_core) != -1 and
                mod.obj_vm.address_compare(addr, mod.module_core + mod.core_size) == -1):
            return mod
        else:
            return None

    def get_module_name(self, module):
        return module.m("name")

    def get_task_pid(self, task):
        return task.pid
'''