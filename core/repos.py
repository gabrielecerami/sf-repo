import copy
import traceback
from colorlog import log, logsummary
import sys
import pprint
from repotypes.git import LocalRepo

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

    def poll(self):
        for project_name in self.projects:
            try:
                logsummary.info('Polling original for new changes. Checking status of all changes.')
                project = Project(project_name, self.projects[project_name], self.base_dir + "/"+ project_name, fetch=True)
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

        # Set up branches hypermap
        # get branches from original
        # self.original_branches = self.underlayer.list_branches('original')

        for branch in project_info['original']['watch-branches']:
            self.branches[branch['name']] = branch
            if 'replica-branch' not in branch:
                self.branches[branch['name']]['replica-branch'] = branch['name']

    def get_new_changes(self, original_branch):
        # change OrderedDict
        replica_branch = self.branches[original_branch]['replica-branch']
        original_changes = self.localrepo.get_commits('remotes/replica/' + original_branch, 'remotes/original/' + original_branch)
        if not original_changes:
            # nothing to do
            return True
        blocked_changes = self.localrepo.replica_remote.get_blocked_changes()
        if blocked_changes:
            print "there are blocked changes that must be solved before continuing"
            return False
        active_changes = self.localrepo.replica_remote.get_changes_data(branch=replica_branch)
        top_of_chain = None
        cherrypickfailed = False
        for uuid, replica_change in active_changes.iteritems():
            if 'neededBy' not in replica_change:
                top_of_chain = replica_change
                active_chain_branch = "replica/changes/%s/%s/%s" % (top_of_chain['number'][-2:], top_of_chain['number'], top_of_chain['patchset_number'])
                break
        for new_change in original_changes:
            replica_change = self.localrepo.find_equivalent_commit(new_change['hash'], active_chain_branch)
            if not replica_change:
                # simple case, original change is not present in replica chain
                # put new change on top.
                try:
                    self.localrepo.cherrypick(new_change)
                except CherryPickfailed:
                    break
                self.localrepo.replica_remote.upload()
            else:
                suggested_solution = "Commit %s from upstream was already cherry-picked as %s in %s patches branch" % (pick_revision, cmd.output[0], patches_branch)
                if replica_change is not top_of_chain:
                # the change is already on the chain, so either is a previously
                # backported change, or the replica was not updated.
                # If it's the same change we skip it, if it's different, we recreate it
                # but we have to respect the order. HOW ?
                # if the now chanee it's not on top of the chain
                # it must become the top of the chain
                    self.localrepo.remove_commits(active_chain_branch [replica_change])
                    try:
                        self.localrepo.cherrypick(new_change)
                    except CherryPickfailed:
                        break
                    self.localrepo.replica_remote.upload()
                else:
                    diff = self.localrepo.check_diffs(replica_change, new_change)
                    if diff:
                        try:
                            self.localrepo.cherrypick(new_change)
                        except CherryPickfailed:
                            break
                        self.localrepo.replica_remote.upload()

        if cherrypickfailed:
            comment = failure
            change_number = self.localrepo.replica_remote.upload("failed_attempts/" % (failed_branch))
            self.localrepo.replica_remote.comment(change_number, comment)


    def poll_branches(self):
        for branch in self.branches:
            self.get_new_changes(branch)

