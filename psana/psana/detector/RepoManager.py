
"""
:py:class:`RepoManager`
=======================

Usage::

      import psana.detector.RepoManager import RepoManageras rm
      kwa = {}
      repoman = rm.init_repoman_and_logger(**kwa)
      # OR:
      repoman = rm.RepoManager(**kwa)
      repoman.save_record_at_start(SCRNAME, tsfmt='%Y-%m-%dT%H:%M:%S%z', adddict={'comment':'none'})

This software was developed for the LCLS-II project.
If you use all or part of it, please give an appropriate acknowledgment.

Created on 2022-01-20 by Mikhail Dubrovin
"""
import os
import sys
#import getpass
import psana.detector.Utils as ut  #OR: import psana.pyalgos.generic.Utils as ut

from psana.detector.dir_root import DIR_REPO, DIR_LOG_AT_START  # DIR_ROOT + '/detector/logs/atstart
SCRNAME = sys.argv[0].rsplit('/')[-1]

import logging
logger = logging.getLogger(__name__)


class RepoManager(object):
    """Supports repository directories/files naming structure
       dirrepo:       self.dirrepo
       dir_dettype:   <dirrepo>/<dettype>
       dir_merge:     <dirrepo>/<dettype>/merge_tmp/
       dir_panel:     <dirrepo>/<dettype>/<panelid>/
       dir_ctype:     <dirrepo>/<dettype>/<panelid>/<ctype>
       dir_logs:      <dirrepo>/<dettype>/logs/
       dir_logs_year: <dirrepo>/<dettype>/logs/<year>/
       logname:       <dirrepo>/<dettype>/logs/<year>/<tstamp>_log_<suffix>.txt
       dir_log_at_start: self.dir_log_at_star
       dir_log_at_start_year: <dir_log_at_start>/<year>/
       logname_at_start:      <dir_log_at_start>/<year>/<year>_<addname>_<suffix>..txt
    """

    def __init__(self, **kwa):
        self.dirrepo     = kwa.get('dirrepo', DIR_REPO).rstrip('/')
        self.dirmode     = kwa.get('dirmode',  0o2775)
        self.filemode    = kwa.get('filemode', 0o664)
        self.umask       = kwa.get('umask', 0o0)
        self.group       = kwa.get('group', 'ps-users')
        self.year        = kwa.get('year', ut.str_tstamp(fmt='%Y'))
        self.tstamp      = kwa.get('tstamp', None)
        self.dettype     = kwa.get('dettype', None)
        self.addname     = kwa.get('addname', 'lcls2')  # 'logrec'
        self.dir_log_at_start = kwa.get('dir_log_at_start', DIR_LOG_AT_START)
        if self.tstamp is None: self.tstamp = ut.str_tstamp(fmt='%Y-%m-%dT%H%M%S')
        self.dirname_log = kwa.get('dirname_log', 'logs')
        self.logsuffix   = kwa.get('logsuffix', '%s_%s' % (SCRNAME, ut.get_login()))  # getpass.getuser()))
        self.savelogfile = kwa.get('savelogfile', False)
        #if 'work' in dirrepo: dir_log_at_start = os.path.join(dirrepo, 'atstart')
        self.logname_tmp = None  # logname before the dettype is defined


    def makedir(self, d):
        """create and return directory d with mode defined in object property"""
        ut.create_directory(d, mode=self.dirmode, umask=self.umask, group=self.group)
        assert os.path.exists(d), 'NOT CREATED DIRECTORY %s' % d
        return d


    def makedir_repo(self):
        return self.makedir(self.dirrepo)


    def dir_in_repo(self, name):
        """DEPRECATED used in utils_roicon.py: return directory <dirrepo>/<name>"""
        return os.path.join(self.dirrepo, name)


    def makedir_in_repo(self, name):
        """DEPRECATED used in app/roicon.py: create and return directory <dirrepo>/<name>"""
        assert os.path.exists(self.makedir_repo())
        return self.makedir(self.dir_in_repo(name))


    def set_dettype(self, dettype):
        if dettype is not None:
            self.dettype = dettype


    def dir_dettype(self, dettype=None):
        """returns path to the dettype directory like
           <dirrepo>/<dettype>
           if sel.dettype is not None or script directory like
           <dirrepo>/scripts/<script-name>
        """
        self.set_dettype(dettype)
        subdir = 'scripts/%s' % SCRNAME if self.dettype is None else self.dettype
        return os.path.join(self.dirrepo, subdir)


    def makedir_dettype(self, dettype=None):
        """creates and returns path to the director type directory like <dirrepo>/[<dettype>]"""
        assert os.path.exists(self.makedir_repo())
        #assert self.dettype is not None
        return self.makedir(self.dir_dettype(dettype))


    def dir_merge(self, dname='merge_tmp'):
        return os.path.join(self.dir_dettype(), dname)


    def makedir_merge(self, dname='merge_tmp'):
        assert os.path.exists(self.makedir_dettype())
        return self.makedir(self.dir_merge(dname))


    def dir_panel(self, panelid):
        """returns path to panel directory like <dirrepo>/<dettype>/<panelid>"""
        assert panelid is not None
        return os.path.join(self.dir_dettype(), panelid)


    def makedir_panel(self, panelid):
        """creates and returns path to panel directory like <dirrepo>/<dettype>/<panelid>"""
        assert os.path.exists(self.makedir_dettype())
        return self.makedir(self.dir_panel(panelid))


    def dir_ctype(self, panelid, ctype): # ctype='pedestals'
        """returns path to the directory like <dirrepo>/<dettype>/<panelid>/<ctype>"""
        assert ctype is not None
        return os.path.join(self.dir_panel(panelid), ctype)


    def makedir_ctype(self, panelid, ctype): # ctype='pedestals'
        """creates and returns path to the directory like <dirrepo>/<dettype>/<panelid>/<ctype>"""
        assert os.path.exists(self.makedir_panel(panelid))
        return self.makedir(self.dir_ctype(panelid, ctype))


    def dir_ctypes(self, panelid, ctypes=('pedestals', 'rms', 'status', 'plots')):
        """defines structure of subdirectories in calibration repository under
           <dirrepo>/<dettype>/<panelid>/...
        """
        return [os.path.join(self.dir_panel(panelid), name) for name in ctypes]


    def makedir_ctypes(self, panelid, ctypes=('pedestals', 'rms', 'status', 'plots')):
        """creates structure of subdirectories in calibration repository under
           <dirrepo>/<dettype>/<panelid>/...
        """
        assert os.path.exists(self.makedir_panel(panelid))
        dirs = self.dir_ctypes(panelid, ctypes=ctypes)
        for d in dirs: self.makedir(d)
        return dirs


    # ALIASES for backward compatability
    def dir_type (self, panelid, ctype): return self.dir_ctype(panelid, ctype)
    def makedir_type (self, panelid, ctype): return self.makedir_ctype(panelid, ctype)
    def dir_types(self, panelid, subdirs): return self.dir_ctypes(panelid, ctypes=subdirs)
    def makedir_types(self, panelid, subdirs): return self.makedir_ctypes(panelid, ctypes=subdirs)


#    def dir_constants(self, dname='constants'):
#        """returns path to the directory like <dirrepo>/<constants>"""
#        return os.path.join(self.dirrepo, dname)


#    def makedir_constants(self, dname='constants'):
#        d = self.makedir_repo()
#        return self.makedir(self.dir_constants(dname))


    def dir_logs(self):
        """returns directory <dirrepo>/<dettype>/logs"""
        return os.path.join(self.dir_dettype(), self.dirname_log)


    def makedir_logs(self):
        """creates and returns directory <dirrepo>/<dettype>/logs"""
        assert os.path.exists(self.makedir_dettype())
        return self.makedir(self.dir_logs())


    def dir_logs_year(self):
        """returns directory <dirrepo>/<dettype>/logs/<year>"""
        return os.path.join(self.dir_logs(), self.year)


    def makedir_logs_year(self):
        """creates and returns directory <dirrepo>/<dettype>/logs/<year>"""
        assert os.path.exists(self.makedir_logs())
        return self.makedir(self.dir_logs_year())


    def logname(self, suffix=None):
        """returns path to the log file <dir_logs_year>/<tstamp>_log_<suffix>.txt"""
        #return None if not self.savelogfile else\
        #       '%s/%s_log_%s.txt' % (self.dir_logs_year(), self.tstamp, str(suffix))
        if suffix is not None: self.logsuffix = suffix
        s = '%s/%s_log_%s.txt' % (self.makedir_logs_year(), self.tstamp, self.logsuffix)
        if self.logname_tmp is None:
           self.logname_tmp = s
        return s


    def makedir_logname(self, suffix):
        self.makedir_logs_year()
        return self.logname(suffix)


    def dir_log_at_start_year(self):
        """returns directory <dir_log_at_start>/<year>"""
        return os.path.join(self.dir_log_at_start, self.year)


    def makedir_log_at_start_year(self):
        """creates and returns directory <dir_log_at_start>/<year>"""
        self.makedir(self.dir_log_at_start)
        assert os.path.exists(self.dir_log_at_start)
        return self.makedir(self.dir_log_at_start_year())


    def logname_at_start(self, suffix):
        """returns path to log-at-start file
           <dir_log_at_start>/<year>/<year>_<addname>_<suffix>.txt
           ex.: <dir_log_at_start>/2023/2023_lcls2_calibman.txt"""
        return '%s/%s_%s_%s.txt' % (self.makedir_log_at_start_year(), self.year, self.addname, suffix)


    def save_record_at_start(self, procname, tsfmt='%Y-%m-%dT%H:%M:%S%z', adddict={}):
        repoman = self
        save_record_at_start(repoman, procname, tsfmt=tsfmt, adddict=adddict)


    def logfile_save(self):
        """The final call to repo-manager which
           - moves originally created logfile under the dettype directory,
           - change its access mode and group ownershp.
        """
        logname = self.logname() # may be different from logname_tmp after definition of dettype
        if logname != self.logname_tmp:
            logname_tmp = os.path.abspath(self.logname_tmp)
            logname = os.path.abspath(logname)
            cmd = 'mv %s %s' % (self.logname_tmp, logname)
            logger.info('\n  move logfile: %s\n  to:           %s\n  and create link' % (logname_tmp, logname))
            os.system(cmd)
            cmd = 'ln -s %s %s' % (logname, logname_tmp)
            os.system(cmd)

        os.chmod(logname, self.filemode)
        ut.change_file_ownership(logname, user=None, group=self.group)


def save_record_at_start(repoman, procname, tsfmt='%Y-%m-%dT%H:%M:%S%z', adddict={}):
    os.umask(repoman.umask)
    logatstart = repoman.logname_at_start(procname)
    fexists = os.path.exists(logatstart)
    d = {'dirrepo':repoman.dirrepo, 'logfile':str(repoman.logname())}
    if adddict: d.update(adddict)
    rec = log_rec_at_start(tsfmt, **d)
    ut.save_textfile(rec, logatstart, mode='a')
    if not fexists:
        ut.set_file_access_mode(logatstart, repoman.filemode)
        ut.change_file_ownership(logatstart, user=None, group=repoman.group)
    logger.info('record: %s\nsaved in file: %s' % (rec, logatstart))


def log_rec_at_start(tsfmt='%Y-%m-%dT%H:%M:%S%z', **kwa):
    """Returns (str) record containing timestamp, login, host, cwd, and command line"""
    s_kwa = ' '.join(['%s:%s'%(k,str(v)) for k,v in kwa.items()])
    return '\n%s user:%s@%s cwd:%s %s command:%s'%\
           (ut.str_tstamp(fmt=tsfmt), ut.get_login(), ut.get_hostname(), ut.get_cwd(), s_kwa, ' '.join(sys.argv))


def init_repoman_and_logger(**kwa):
    from psana.detector.UtilsLogging import init_logger, init_stream_handler, init_file_handler

    #dirrepo  = kwa.get('dirrepo', './work')
    #dirmode  = kwa.get('dirmode', 0o2775)
    #umask    = kwa.get('umask', 0o0)
    #dettype  = kwa.get('dettype', 'undefined')
    #year     = kwa.get('year', ut.str_tstamp(fmt='%Y'))
    #tstamp   = kwa.get('tstamp', ut.str_tstamp(fmt='%Y-%m-%dT%H%M%S'))
    #dirname_log = kwa.get('dirname_log', 'logs')
    #dir_log_at_start = kwa.get('dir_log_at_start', DIR_LOG_AT_START)
    #filemode = kwa.get('filemode', 0o664)

    logsuffix   = kwa.get('logsuffix', '%s_%s' % (SCRNAME, ut.get_login()))  # getpass.getuser()))
    savelogfile = kwa.get('savelogfile', True)
    parser      = kwa.get('parser', None)
    logmode     = kwa.get('logmode', 'INFO')
    group       = kwa.get('group', 'ps-users')
    fmt         = kwa.get('fmt', '[%(levelname).1s] %(filename)s L%(lineno)04d %(message)s')

    repoman = RepoManager(**kwa)

    init_stream_handler(loglevel=logmode, fmt=fmt)

    logfname = repoman.makedir_logname(logsuffix)
    if savelogfile:
        init_file_handler(logfname=logfname, loglevel=logmode, **kwa)  # loglevel=logmode, filemode=filemode, group=group, fmt=fmt
    repoman.save_record_at_start(SCRNAME, adddict={}) #tsfmt='%Y-%m-%dT%H:%M:%S%z'

    if parser is not None:
        logger.info(ut.info_parser_arguments(parser))

    return repoman


if __name__ == "__main__":
    dirrepo = './work'
    suffix = 'testfname'
    procname = 'testproc-%s' % ut.get_login()
    repoman = RepoManager(dirrepo=dirrepo, dettype='testproc', dir_log_at_start='%s/log-at-start' % dirrepo)
    print('makedir_logs %s' % repoman.makedir_logs())
    print('logname %s' % repoman.logname(procname))
    print('logname_at_start %s' % repoman.logname_at_start(suffix))
    repoman.save_record_at_start()

# EOF
