import copy
import traceback
from colorlog import log, logsummary
import sys
import pprint
from repotypes.git import LocalRepo
from datastructures import Backport
from exceptions import *

class Repos(object):

    def __init__(self, projects_conf, base_dir, filter_projects=None, filter_method=None, filter_branches=None, fetch=True):
        self.projects = dict()
        self.projects_conf = projects_conf
        self.base_dir = base_dir
        # restrict project to operate on
        projects = copy.deepcopy(projects_conf['projects'])
        project_list = list(projects)
        if filter_method:
            new_projects = dict()
            log.info('Filtering projects with watch method: %s' % filter_method)
            for project_name in projects:
                if projects[project_name]['original']['watch-method'] == filter_method:
                    new_projects[project_name] = projects[project_name]
            projects = new_projects
        if filter_projects:
            new_projects = dict()
            log.info('Filtering projects with names: %s' % filter_projects)
            project_names = filter_projects.split(',')
            for project_name in project_names:
                if project_name not in project_list:
                    log.error("Project %s is not present in projects configuration" % project_name)
                try:
                    new_projects[project_name] = projects[project_name]
                except KeyError:
                    log.warning("Project %s already discarded by previous filter" % project_name)
            projects = new_projects
        if filter_branches:
            log.info("Filtering branches: %s" % filter_branches)
            branches = filter_branches.split(',')
            for project_name in projects:
                projects[project_name]['original']['watch-branches'] = branches

        if not projects:
            log.error("Project list to operate on is empty")
            raise ValueError
        log.debugvar('projects')

        logsummary.info("initializing and updating local repositories for relevant projects")
        self.projects = projects

    def poll(self, fetch=True):
        for project_name in self.projects:
            try:
                logsummary.info('Polling original for new changes. Checking status of all changes.')
                project = Project(project_name, self.projects[project_name], self.base_dir + "/"+ project_name, fetch=fetch)
                logsummary.info("Project: %s initialized" % project_name)
                project.poll_branches()
            except Exception, e:
                traceback.print_exc(file=sys.stdout)
                log.error(e)
                logsummary.error("Project %s skipped, reason: %s" % (project_name, e))

class Project(object):

    def __init__(self, project_name, project_info, local_dir, fetch=True):
        self.project_name = project_name
        self.commits = dict()
        self.branches = dict()
        self.base_tags = dict()

        log.info('Current project:\n' + pprint.pformat(project_info))
        self.original_project = project_info['original']
        self.replica_project = project_info['replica']
        self.rev_deps = None
        if 'rev-deps' in project_info:
            self.rev_deps = project_info['rev-deps']

        self.localrepo = LocalRepo(project_name, local_dir)

        # Set up remotes
        self.localrepo.set_replica(self.replica_project['location'], self.replica_project['name'], fetch=fetch)
        self.localrepo.set_original(self.original_project['type'], self.original_project['location'], self.original_project['name'], fetch=fetch)
        self.original_repo = self.localrepo.remotes['original']
        self.replica_repo = self.localrepo.remotes['replica']

        # Set up branches hypermap
        # get branches from original
        # self.original_branches = self.underlayer.list_branches('original')

        for branch in project_info['original']['watch-branches']:
            self.branches[branch['name']] = branch
            if 'replica-branch' not in branch:
                self.branches[branch['name']]['replica-branch'] = branch['name']
            if 'base-tag' in branch:
                self.base_tags[branch['name']] = branch['base-tag']
            else:
                self.base_tags[branch['name']] = self.localrepo.find_latest_tag("replica/" + branch['name'])


    def poll_branches(self):
        for branch in self.branches:
            replica_branch = self.branches[branch]['replica-branch']
            new_commits = self.localrepo.get_commits('remotes/replica/' + branch, 'remotes/original/' + branch, no_merges=True)
            new_changes = self.original_repo.local_track.get_changes([commit['hash'] for commit in new_commits], branch='remotes/original' + branch)
            if not new_changes:
                # nothing to do
                log.info("No new changes")
                return True
            blocked_changes = self.localrepo.replica_remote.get_blocked_changes()
            if blocked_changes:
                print "there are blocked changes that must be solved before continuing"
                return False
            # TODO: backport analysis
            # how many backports, how many commits ? in whic order ?
            # how many already exist ?
            # compare_changes list with backports list
            # what should be merged, what should be skipped ?
            # how do we advance local repo ?
            for uuid, change in new_changes.iteritems():
                # backport chain might have been modified, force fetch
                self.localrepo.fetch_changes('replica')
                change.prepare_backport(self.replica_repo, replica_branch)
                try:
                    change.backport.auto_attempt(replica_branch)
                except CherryPickFailed, e:
                    log.critical("cherry pick failed")
                    change.backport.request_human_resolution(e)
                    raise
                except UploadError:
                    log.crit("upload failed")
                    raise


