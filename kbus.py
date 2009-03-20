"""Python code for using and testing the kbus kernel module.

Intended for use with (for instance) nose -- so, for instance::

    $ cd kernel_module
    $ make
    $ nosetests kbus.py -d
    .
    ----------------------------------------------------------------------
    Ran 1 test in 0.026s

    OK

To get the doctests (for instance, in Message) as well, try::

    nosetests kbus.py -d --doctest-tests --with-doctest

On Ubuntu, if I want ordinary users (in the admin group) to be able to
read/write '/dev/kbus0' then I need to have a file '/etc/udec/rules.d/45-kbus'
which contains::

    KERNEL=="kbus0",  MODE="0666", GROUP="admin"

Other operating systems will have other mechanisms, and on an embedded system
it is likely enough not to do this, as the "user" will be root.
"""

# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is the KBUS Lightweight Linux-kernel mediated
# message system
#
# The Initial Developer of the Original Code is Kynesim, Cambridge UK.
# Portions created by the Initial Developer are Copyright (C) 2009
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Kynesim, Cambridge UK
#   Tibs <tony.ibbs@gmail.com>
#
# ***** END LICENSE BLOCK *****

import sys
import os
import subprocess
import nose
import fcntl
import time
import ctypes
import array
import errno

# Kernel definitions for ioctl commands
# Following closely from #include <asm[-generic]/ioctl.h>
# (and with some thanks to http://wiki.maemo.org/Programming_FM_radio/)
_IOC_NRBITS   = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS  = 2

_IOC_NRSHIFT   = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT  = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_NONE  = 0
_IOC_WRITE = 1
_IOC_READ  = 2

# Mustn't use "type" as an argument, since Python already has it...
def _IOC(d,t,nr,size):
    return ((d << _IOC_DIRSHIFT) | (ord(t) << _IOC_TYPESHIFT) | 
            (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT))
def _IO(t,nr):
    return _IOC(_IOC_NONE, t, nr, 0)
def _IOW(t,nr,size):
    return _IOC(_IOC_WRITE, t, nr, size)
def _IOR(t,nr,size):
    return _IOC(_IOC_READ, t, nr, size)
def _IOWR(t,nr,size):
    return _IOC(_IOC_READ | _IOC_WRITE, t, nr, size)

KBUS_IOC_MAGIC = 'k'
KBUS_IOC_RESET	  = _IO(KBUS_IOC_MAGIC,   1)
KBUS_IOC_BIND	  = _IOW(KBUS_IOC_MAGIC,  2, ctypes.sizeof(ctypes.c_char_p))
KBUS_IOC_UNBIND	  = _IOW(KBUS_IOC_MAGIC,  3, ctypes.sizeof(ctypes.c_char_p))
KBUS_IOC_BOUNDAS  = _IOR(KBUS_IOC_MAGIC,  4, ctypes.sizeof(ctypes.c_char_p))
KBUS_IOC_REPLIER  = _IOWR(KBUS_IOC_MAGIC, 5, ctypes.sizeof(ctypes.c_char_p))
KBUS_IOC_NEXTLEN  = _IO(KBUS_IOC_MAGIC,   6)

def setup_module():
    retcode = system('sudo insmod kbus.ko kbus_num_devices=3')
    #retcode = system('sudo insmod kbus.ko')
    assert retcode == 0
    # Via the magic of hotplugging, that should cause our device to exist
    # ...eventually
    time.sleep(1)

def teardown_module():
    retcode = system('sudo rmmod kbus')
    assert retcode == 0
    # Via the magic of hotplugging, that should cause our device to go away
    # ...eventually
    time.sleep(1)
    assert not os.path.exists("/dev/kbus0")

# Let's be good and not use os.system...
def system(command):
    """Taken from the Python reference manual. Thank you.
    """
    try:
        retcode = subprocess.call(command, shell=True)
        if retcode < 0:
            print "'%s' was terminated by signal %s"%(command,-retcode)
        else:
            print "'%s' returned %s"%(command,retcode)
        return retcode
    except OSError, e:
        print "Execution of '%s' failed: %s"%(command,e)

class Message(object):
    """A wrapper for a KBUS message

    A Message can be created in a variety of ways. Perhaps most obviously:

        >>> msg1 = Message('$.Fred',data='1234')
        >>> msg1
        Message('$.Fred', array('L', [875770417L]), 0L, 0L, 0x00000000)

    Note that if the 'data' is specified as a string, there must be a multiple
    of 4 characters -- i.e., it must "fill out" an array of unsigned int-32
    words.

    A Message can be constructed from another message directly:

        >>> msg2 = Message(msg1)
        >>> msg2 == msg1
        True

    or from the '.extract()' tuple:

        >>> msg3 = Message(msg1.extract())
        >>> msg3 == msg1
        True

    (note this ignores the zeroth element of that tuple, which is a message
    id), or from an equivalent list::

        >>> msg3 = Message(list(msg1.extract()))
        >>> msg3 == msg1
        True

    Or one can use an array (of unsigned 32-bit words):

        >>> msg1.array
        array('L', [1937072747L, 0L, 0L, 0L, 0L, 6L, 1L, 1917201956L, 25701L, 875770417L, 1801614707L])
        >>> msg3 = Message(msg1.array)
        >>> msg3 == msg1
        True

    or the same thing represented as a string:

        >>> msg_as_string = msg1.array.tostring()
        >>> msg4 = Message(msg_as_string)
        >>> msg4 == msg1
        True

    Our internal values are:

    - 'array', which is the actual message data, as an array.array('L',...)
      (i.e., unsigned 32-bit words)
    - 'length', which is the length of that array (again, as 32-bit words)
    """

    START_GUARD = 0x7375626B
    END_GUARD = 0x6B627573

    def __init__(self, arg, data=None, to=0, from_=0, flags=0):
        """Initialise a Message.
        """

        if isinstance(arg,Message):
            self.array = array.array('L',arg.array)
        elif isinstance(arg,tuple) or isinstance(arg,list):
            # A tuple from .extract(), or an equivalent tuple/list
            if len(arg) != 6:
                raise ValueError("Tuple arg to Message() must have"
                        " 6 values, not %d"%len(arg))
            else:
                self.array = self._from_data(arg[-2],data=arg[-1],to=arg[1],
                                             from_=arg[2],flags=arg[3])
        elif isinstance(arg,str) and arg.startswith('$.'):
            # It looks like a message name
            self.array = self._from_data(arg,data,to,from_,flags)
        elif arg:
            # Assume it's sensible data...
            # (note that even if 'arg' was an array of the correct type.
            # we still want to take a copy of it, so the following is
            # reasonable enough)
            self.array = array.array('L',arg)
        else:
            raise ValueError,'Argument %s does not seem to make sense'%repr(arg)

        # Make sure the result *looks* like a message
        self._check()

        # And I personally find it useful to have the length available
        self.length = len(self.array)

    def _from_data(self,name,data=None,to=0,from_=0,flags=0):
        """Set our data from individual arguments.

        Note that 'data' must be:
        
        1. an array.array('L',...) instance, or
        2. a string, or something else compatible, which will be converted to
           the above, or
        3. None.
        """

        msg = array.array('L',[])

        # Start guard, id, to, from, flags -- all defaults for the moment
        msg.append(self.START_GUARD)    # start guard
        msg.append(0)                   # id -- assigned by kbus
        msg.append(to)
        msg.append(from_)
        msg.append(flags)

        # We add the *actual* length of the name
        msg += array.array('L',[len(name)])
        # But remember that the message name itself needs padding out to
        # 4-bytes
        # ...this is about the nastiest way possible of doing it...
        while len(name)%4:
            name += '\0'

        # If it's not already an array of the right type, then let's try and
        # make it so
        if data == None:
            pass
        elif isinstance(data,array.array) and data.typecode == 'L':
            pass
        else:
            data = array.array('L',data)

        # Next comes data length (which we now know will be in the right units)
        if data:
            msg.append(len(data))
        else:
            msg.append(0)

        # Then the name
        name_array = array.array('L',name)
        msg += name_array

        # And, if we have any, the data
        if data:
            msg += data

        # And finally remember the end guard
        msg.append(self.END_GUARD)

        return msg

    def _check(self):
        """Perform some basic sanity checks on our data.
        """
        # XXX Make the reporting of problems nicer for the user!
        assert self.array[0] == self.START_GUARD
        assert self.array[-1] == self.END_GUARD
        name_len_bytes = self.array[5]
        name_len = (name_len_bytes+3) / 4   # in 32-bit words
        data_len = self.array[6]            # in 32-bit words
        if name_len_bytes < 3:
            raise ValueError("Message name is %d long, minimum is 3"
                             " (e.g., '$.*')"%name_len_bytes)
        assert data_len >= 0
        assert 8 + name_len + data_len == len(self.array)

    def __repr__(self):
        (id,to,from_,flags,name,data_array) = self.extract()
        args = [repr(name),
                repr(data_array),
                repr(to),
                repr(from_),
                '0x%08x'%flags]
        return 'Message(%s)'%(', '.join(args))

    def __eq__(self,other):
        return self.array == other.array

    def equivalent(self,other):
        """Returns true if the two messages only differ in 'id' and 'from'
        """
        if self.length != other.length:
            return False
        # Somewhat clumsily...
        parts1 = list(self.extract())
        parts2 = list(other.extract())
	parts1[0] = parts2[0]	# id
	parts1[2] = parts2[2]	# from
        return parts1 == parts2

    def extract(self):
        """Return our parts as a tuple.

        The values are returned in something approximating the order
        within the message itself:

            (id,to,from_,flags,name,data_array)
        """

        # Sanity check:
        assert self.array[0] == self.START_GUARD
        assert self.array[-1] == self.END_GUARD

        msg = self.array
        id = msg[1]
        to = msg[2]
        from_ = msg[3]
        flags = msg[4]
        name_len = msg[5]
        data_len = msg[6]
        name_array_len = (name_len+3)/4
        name_array = msg[7:7+name_array_len]
        name = name_array.tostring()
        # Note that if the message was well constructed, any padding bytes
        # at the end of the name will be '\0', and thus not show when printed
        #print '%d<%s>'%(len(name),name),
        # Make sure we remove the padding bytes
        name = name[:name_len]

        data_offset = 7+name_array_len
        data_array = msg[data_offset:data_offset+data_len]
        #print '<%s>'%(data_array.tostring())

        return (id,to,from_,flags,name,data_array)

    def to_file(self,f):
        """Write the Message's data to a file.

        'f' is the file object to write to (expected to be an instance of
        opening '/dev/kbus0').

        NB: flushes the output when it's done.
        """
        self.array.tofile(f)
        f.flush()

        return self

class KbufBindStruct(ctypes.Structure):
    """The datastucture we need to describe a KBUS_IOC_BIND argument
    """
    _fields_ = [('replier',    ctypes.c_uint),
                ('guaranteed', ctypes.c_uint),
                ('len',        ctypes.c_uint),
                ('name',       ctypes.c_char_p)]

def bind(f,name,replier=True,guaranteed=False):
    """Bind the given name to the file descriptor.

    If 'replier', then we are binding as the only fd that can reply to this
    message name.

        XXX Is 'True' actually a sensible default for 'replier'? Normally one
        XXX *does* want a single replier, but I've found myself calling 'bind'
        XXX multiple times with the same message name, and forgetting that I
        XXX need to say the listeners are not repliers. Is there a better
        XXX (separate) error code that the ioctl could return in this case?

    If 'guaranteed', then we require that *all* messages to us be delivered,
    otherwise kbus may drop messages if necessary.
    """
    arg = KbufBindStruct(replier,guaranteed,len(name),name)
    return fcntl.ioctl(f, KBUS_IOC_BIND, arg);

def unbind(f,name,replier=True,guaranteed=False):
    """Unbind the given name from the file descriptor.

    The arguments need to match the binding that we want to unbind.
    """
    arg = KbufBindStruct(replier,guaranteed,len(name),name)
    return fcntl.ioctl(f, KBUS_IOC_UNBIND, arg);

def bound_as(f):
    """Return the 'bind number' for this file descriptor.
    """
    # Instead of using a ctypes.Structure, we can retrieve homogenious
    # arrays of data using, well, arrays. This one is a bit minimalist.
    id = array.array('L',[0])
    fcntl.ioctl(f, KBUS_IOC_BOUNDAS, id, True)
    return id[0]

def next_len(f):
    """Return the length of the next message (if any) on this file descriptor
    """
    return fcntl.ioctl(f, KBUS_IOC_NEXTLEN, 0)

class KbufListenerStruct(ctypes.Structure):
    """The datastucture we need to describe a KBUS_IOC_REPLIER argument
    """
    _fields_ = [('return_id', ctypes.c_uint),
                ('len',  ctypes.c_uint),
                ('name', ctypes.c_char_p)]

def find_listener(f,name):
    """Find the id of the replier (if any) for this message.

    Returns None if there was no replier, otherwise the replier's id.
    """
    arg = KbufListenerStruct(0,len(name),name)
    retval = fcntl.ioctl(f, KBUS_IOC_REPLIER, arg);
    if retval:
        return arg.return_id
    else:
        return None

def read_bindings(names):
    """Read the bindings from /proc/kbus/bindings, and return a list

    /proc/kbus/bindings gives us data like::

            0: 10 R T $.Fred
            0: 11 L T $.Fred.Bob
            0: 12 R F $.William

    'names' is a dictionary of file descriptor binding id to string (name)
    - for instance:
    
        { 10:'f1', 11:'f2' }

    If there is no entry in the 'names' dictionary for a given id, then the
    id will be used (as an integer).
        
    Thus with the above we would return a list of the form::

        [ ('f1',True,True,'$.Fred'), ('f2',False,True,'$.Fred.Bob'),
          (12,True,False,'$.William' ]
    """
    f = open('/proc/kbus/bindings')
    l = f.readlines()
    f.close()
    bindings = []
    for line in l:
        # 'dev' is the device index (default is 0, may be 0..9 depending on how
        # many /dev/kbus<N> devices there are).
        # For the moment, we're going to ignore it.
        dev,id,rep,all,name = line.split()
        id = int(id)
        if id in names:
            id = names[int(id)]
        if rep == 'R':          # Replier
            rep = True
        elif rep == 'L':        # (just a) Listener
            rep = False
        else:
            raise ValueError,"Got replier '%c' when expecting 'R' or 'L'"%rep
        if all == 'T':          # Want ALL messages
            all = True
        elif all == 'F':        # Willing to miss some messages
            all = False
        else:
            raise ValueError,"Got all '%c' when expecting 'T' or 'F'"%all
        bindings.append((id,rep,all,name))
    return bindings

def str_rep(rep):
    if rep:
        return 'R'
    else:
        return 'L'

def str_all(all):
    if all:
        return 'T'
    else:
        return 'F'

def bindings_match(bindings):
    """Look up the current bindings and check they match the list.

    'bindings' is a sequence of tuples, each of the form:

        ( file_descriptor, True|False, True|False, name )

    so for instance:

        ( (f,True,True,'$.Fred'), (g,False,False,'$.JimBob') )

    where the first True means the binding is for a replier (or not), and the
    second means it wants to guarantee to receive all its messages (or not).

    The function reads the contents of /proc/kbus/bindings. It translates each
    file descriptor to a listener id using ``bound_as``, and thus converts
    'bindings' to an equivalent list.

    Silently returns True if the bindings in /proc/kbus/bindings match
    those expected, returns False (and prints out the mismatch) if they do not.
    """
    testwith = []
    names = {}
    for (fd,rep,all,name) in bindings:
        if fd not in names:
            names[fd] = bound_as(fd)
        testwith.append((bound_as(fd),rep,all,name))

    actual = read_bindings(names)

    # And compare the two lists - ideally they should match
    # (although we don't want to care about order, I think)
    actual.sort()
    testwith.sort()
    if actual == testwith:
        return True

    # If they're not the same, we need to let the user know in some not too
    # unfriendly manner
    found    = set(actual)
    expected = set(testwith)
    print 'The contents of /proc/kbus/bindings is not as expected'
    if len(found):
        print 'The following were expected but not found:'
        for f,r,a,n in expected-found:
            print '  %10u %c %c %s'%(f,str_rep(r),str_all(a),n)
    if len(expected):
        print 'The following were found but not expected:'
        for f,r,a,n in found-expected:
            print '  %10u %c %c %s'%(f,str_rep(r),str_all(a),n)
    return False

def check_IOError(expected_errno,fn,*stuff):
    """When calling apply(fn,stuff), check for IOError with the given errno.

    Check that is what happens...
    """
    try:
        apply(fn,stuff)
        # We're not expecting to get here...
        assert False, 'Applying %s did not fail with IOError'%stuff
    except IOError, e:
        actual_errno = e.args[0]
        errno_name = errno.errorcode[actual_errno]
        expected_errno_name = errno.errorcode[expected_errno]
        assert actual_errno == expected_errno, \
                'expected %s, got %s'%(expected_errno_name,errno_name)
    except Exception, e:
        print e
        assert False, 'Applying %s failed with %s, not IOError'%(stuff,sys.exc_type)

class TestKernelModule:

    # A dictionary linking open /dev/kbus0 instances to replier True/False
    # and guaranteed-delivery True/False flags, and message names - so, for
    # instance:
    #
    #    bindings[f] = [(True,False,'$.Fred.Jim.Bob'), (False,True,'$.Fred')]
    #
    # (the order in the tuple matches that in the /proc/kbus/bindings file).
    #
    # Automatically managed by the local bind and unbind *methods*
    bindings = {}

    def bind(self,f,name,replier=True,guaranteed=False):
        """A wrapper around the 'bind' function. to keep track of bindings.
        """
        bind(f,name,replier,guaranteed)
        TestKernelModule.bindings[f].append( (replier,guaranteed,name) )

    def unbind(self,f,name,replier=True,guaranteed=False):
        """A wrapper around the 'unbind' function, to keep track of bindings.
        """
        unbind(f,name,replier,guaranteed)
        l = TestKernelModule.bindings[f]
        # If there are multiple matches, we'll delete the first,
        # which is what we want (well, to delete a single instance)
        for index,thing in enumerate(l):
            if thing[-1] == name:       # the name is always the last element
                del l[index]
                break
        # No matches shouldn't occur, but let's ignore it anyway

    def attach(self,mode):
        """A wrapper around opening /dev/kbus0, to keep track of bindings.
        """
        f = open('/dev/kbus0',mode)
        if f:
            TestKernelModule.bindings[f] = []
        return f

    def detach(self,f):
        """A wrapper around closing a /dev/kbus0 instance.
        """
        del TestKernelModule.bindings[f]
        return f.close()

    def _check_bindings(self):
        """Check the bindings we think we have match those of kbus
        """
        expected = []
        for fd,l in TestKernelModule.bindings.items():
            for r,a,n in l:
                expected.append( (fd,r,a,n) )
        assert bindings_match(expected)

    def _check_read(self,f,expected):
        """Check that we can read back an equivalent message to 'expected'
        """
        if expected:
            data = f.read(expected.length*4)
            assert data != None
            new_message = Message(data)
            assert expected.equivalent(new_message)
        else:
            #nose.tools.assert_raises(EOFError,f.read,1)
            data = f.read(1)
            assert data == ''

    def test_readonly(self):
        """If we open the device readonly, we can't do much(!)
        """
        f = self.attach('rb')
        assert f != None
        try:
            # Nothing to read
            assert f.read(1) == ''

            # We can't write to it
            msg2 = Message('$.Fred','data')
            check_IOError(errno.EBADF,msg2.to_file,f)
        finally:
            assert self.detach(f) is None

    def test_readwrite_kbus0(self):
        """If we open the device read/write, we can read and write.
        """
        f = self.attach('wb+')
        assert f != None

        try:
            self.bind(f,'$.B')
            self.bind(f,'$.C')

            # We start off with no message
            self._check_read(f,None)

            # We can write a message and read it back
            msg1 = Message('$.B','data')
            msg1.to_file(f)
            self._check_read(f,msg1)

            # We can write a message and read it back, again
            msg2 = Message('$.C','fred')
            msg2.to_file(f)
            self._check_read(f,msg2)

            # If we try to write a message that nobody is listening for,
            # we get an appropriate error
            msg3 = Message('$.D','fred')
            check_IOError(errno.EADDRNOTAVAIL,msg3.to_file,f)

        finally:
            assert self.detach(f) is None

    def test_two_opens_kbus0(self):
        """If we open the device multiple times, they communicate
        """
        f1 = self.attach('wb+')
        assert f1 != None
        try:
            f2 = self.attach('wb+')
            assert f2 != None
            try:
                # Both files listen to both messages
                self.bind(f1,'$.B',False)
                self.bind(f1,'$.C',False)
                self.bind(f2,'$.B',False)
                self.bind(f2,'$.C',False)

                # Nothing to read at the start
                self._check_read(f1,None)
                self._check_read(f2,None)

                # If we write, we can read appropriately
                msg1 = Message('$.B','data')
                msg1.to_file(f1)
                self._check_read(f2,msg1)
                self._check_read(f1,msg1)

                msg2 = Message('$.C','data')
                msg2.to_file(f2)
                self._check_read(f1,msg2)
                self._check_read(f2,msg2)
            finally:
                assert self.detach(f2) is None
        finally:
            assert self.detach(f1) is None

    def test_bind(self):
        """Initial ioctl/bind test.
        """
        f = self.attach('wb+')
        assert f != None

        try:
            # - BIND
            # The "Bind" ioctl requires a proper argument
            check_IOError(errno.EINVAL, fcntl.ioctl,f, KBUS_IOC_BIND, 0)
            # Said string must not be zero length
            check_IOError(errno.EINVAL, self.bind, f, '', True)
            # At some point, it will have restrictions on what it *should* look
            # like
            self.bind(f,'$.Fred')
            # - UNBIND
            check_IOError(errno.EINVAL, fcntl.ioctl,f, KBUS_IOC_UNBIND, 0)
            check_IOError(errno.EINVAL, self.unbind,f, '', True)
            self.unbind(f,'$.Fred')
        finally:
            assert self.detach(f) is None

    def test_many_bind_1(self):
        """Initial ioctl/bind test -- make lots of bindings
        """
        f = self.attach('wb+')
        assert f != None

        try:
            self.bind(f,'$.Fred')
            self.bind(f,'$.Fred.Jim')
            self.bind(f,'$.Fred.Bob')
            self.bind(f,'$.Fred.Jim.Bob')
        finally:
            assert self.detach(f) is None

    def test_many_bind_2(self):
        """Initial ioctl/bind test -- make lots of the same binding
        """
        f = self.attach('wb+')
        assert f != None

        try:
            self.bind(f,'$.Fred')
            self.bind(f,'$.Fred',False)
            self.bind(f,'$.Fred',False)
            self.bind(f,'$.Fred',False)
        finally:
            assert self.detach(f) is None

    def test_many_bind_3(self):
        """Initial ioctl/bind test -- multiple matching bindings/unbindings
        """
        f = self.attach('wb+')
        assert f != None

        try:
            self.bind(f,'$.Fred')       # But remember, only one replier
            self.bind(f,'$.Fred',False)
            self.bind(f,'$.Fred',False)
            self.unbind(f,'$.Fred')
            self.unbind(f,'$.Fred',False)
            self.unbind(f,'$.Fred',False)
            # But not too many
            check_IOError(errno.EINVAL, self.unbind,f, '$.Fred')
            check_IOError(errno.EINVAL, self.unbind,f, '$.Fred',False)
            # We can't unbind something we've not bound
            check_IOError(errno.EINVAL, self.unbind,f, '$.JimBob',False)
        finally:
            assert self.detach(f) is None

    def test_bind_more(self):
        """Initial ioctl/bind test - with more bindings.
        """
        f1 = self.attach('wb+')
        assert f1 != None
        try:
            f2 = self.attach('wb+')
            assert f2 != None
            try:
                # We can bind and unbind
                self.bind(f1,'$.Fred',replier=True)
                self.unbind(f1, '$.Fred',replier=True)
                self.bind(f1,'$.Fred',replier=False)
                self.unbind(f1, '$.Fred',replier=False)
                # We can bind many times
                self.bind(f1,'$.Fred',replier=False)
                self.bind(f1,'$.Fred',replier=False)
                self.bind(f1,'$.Fred',replier=False)
                # But we can only have one replier
                self.bind(f1,'$.Fred',replier=True)
                check_IOError(errno.EADDRINUSE, self.bind,f1, '$.Fred',True)

                # Two files can bind to the same thing
                self.bind(f1,'$.Jim.Bob',replier=False)
                self.bind(f2,'$.Jim.Bob',replier=False)
                # But we can still only have one replier
                # (the default is to bind a replier, since we expect that in
                # general there should be one, and if the binder is *not* a
                # replier, they probably should have thought about this).
                self.bind(f1,'$.Jim.Bob')
                check_IOError(errno.EADDRINUSE, self.bind,f2, '$.Jim.Bob')

                # Oh, and not all messages need to be received
                # - in our interfaces, we default to allowing kbus to drop
                # messages if necessary
                self.bind(f1,'$.Jim.Bob',replier=False,guaranteed=True)
                self.bind(f1,'$.Fred',replier=False,guaranteed=True)
            finally:
                assert self.detach(f2) is None
        finally:
            assert self.detach(f1) is None

    def test_bindings_match1(self):
        """Check that bindings match inside and out.
        """
        f1 = self.attach('wb+')
        assert f1 != None
        try:
            f2 = self.attach('wb+')
            assert f2 != None
            try:
                self.bind(f1,'$.Fred')
                self.bind(f1,'$.Fred.Jim')
                self.bind(f1,'$.Fred.Bob')
                self.bind(f1,'$.Fred.Jim.Bob')
                self.bind(f1,'$.Fred.Jim.Derek',False)
                # /proc/kbus/bindings should reflect all of the above, and none other
                self._check_bindings()
                self.bind(f2,'$.Fred.Jim.Derek',False)
                self.bind(f2,'$.William',False)
                self.bind(f2,'$.William',False)
                self.bind(f2,'$.William',False)
                self.bind(f1,'$.Fred.Jim.Bob.Eric')
                self._check_bindings()
            finally:
                assert self.detach(f2) is None
        finally:
            assert self.detach(f1) is None
        # And now all of the bindings *should* have gone away
        self._check_bindings()

    def test_rw_single_file(self):
        """Test reading and writing two messages on a single file
        """
        f = self.attach('wb+')
        assert f != None
        try:

            name1 = '$.Fred.Jim'
            data1 = array.array('L','datadata')

            name2 = '$.Fred.Bob.William'
            data2 = array.array('L','This is surely some data')

            # Bind so that we can write/read the first, but not the second
            self.bind(f,name1)
            self.bind(f,'$.William')

            msg1 = Message(name1,data=data1)
            msg1.to_file(f)
            print 'Wrote:',msg1

            # There are no listeners for '$.Fred.Bob.William'
            msg2 = Message(name2,data=data2)
            check_IOError(errno.EADDRNOTAVAIL, msg2.to_file, f)

            data = f.read(msg1.length*4)
            msg1r = Message(data)
            print 'Read: ',msg1r

            data = f.read(msg2.length*4)
            assert len(data) == 0

            # The message read should match in all but the "id" and "from" fields
            assert msg1.equivalent(msg1r)

            # There shouldn't be anything else to read
            assert f.read(1) == ''

        finally:
            assert self.detach(f) is None

    def test_read_write_2files(self):
        """Test reading and writing between two files.
        """
        f1 = self.attach('wb+')
        assert f1 != None
        try:
            f2 = self.attach('wb+')
            assert f2 != None
            try:
                self.bind(f1,'$.Fred')
                self.bind(f1,'$.Fred',False)
                self.bind(f1,'$.Fred',False)

                self.bind(f2,'$.Jim')

                # Writing to $.Fred on f1 - writes messages N, N+1. N+2
                msgF = Message('$.Fred','data')
                msgF.to_file(f1)

                # No one is listening for $.William
                msgW = Message('$.William')
                check_IOError(errno.EADDRNOTAVAIL,msgW.to_file,f1)
                check_IOError(errno.EADDRNOTAVAIL,msgW.to_file,f2)

                # Writing to $.Jim on f1 - writes message N+3
                msgJ = Message('$.Jim','moredata')
                msgJ.to_file(f1)

                # Reading f1 - message N
                assert next_len(f1) == msgF.length*4
                # By the way - it's still the next length until we read
                assert next_len(f1) == msgF.length*4
                data = Message(f1.read(msgF.length*4))
                # Extract the message id -- this is N
                n0 = data.extract()[0]

                # Reading f2 - should be message N+3 ...
                assert next_len(f2) == msgJ.length*4
                data = Message(f2.read(msgJ.length*4))
                n3 = data.extract()[0]
                assert n3 == n0+3

                # Reading f1 - should be message N+1 ...
                assert next_len(f1) == msgF.length*4
                data = Message(f1.read(msgF.length*4))
                n1 = data.extract()[0]
                assert n1 == n0+1

                # Reading f1 - should be message N+2 ...
                assert next_len(f1) == msgF.length*4
                data = Message(f1.read(msgF.length*4))
                n2 = data.extract()[0]
                assert n2 == n0+2

                # No more messages on f1
                assert next_len(f1) == 0
                assert f1.read(1) == ''

                # No more messages on f2
                assert next_len(f2) == 0
                assert f1.read(2) == ''
            finally:
                assert self.detach(f2) is None
        finally:
            assert self.detach(f1) is None

# vim: set tabstop=8 shiftwidth=4 expandtab:
