import copy
import traceback
from colorlog import log, logsummary
import sys
import pprint
from repotypes.git import LocalRepo
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
            original_branch = 'remotes/original/' + branch
            base_ref = self.base_tags[branch]
            self.localrepo.create_base_pick_branch(replica_branch, base_ref)
            commits_fromtag = self.localrepo.get_commits(self.base_tags[branch], original_branch)
            original_changes = self.original_repo.local_track.get_changes([commit['hash'] for commit in commits_fromtag], branch='remotes/original' + branch)
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
            # Analize backports to do, do them all at once,
            # stop at first troublesome cherrypick, but merge the rest
            # put all local-only backports on top
            # XXX: USE OLD MERGE TO COMMIT METHOD
            backports = self.replica_repo.get_changes(branch=replica_branch, chain=True, results_key='revision')
            backports_list = list(backports)
            preventive_backports_list = set(backports)
            log.debugvar('backports')
            if backports:
                tb_id, top_backport = backports.popitem(last=True)
                chain_ref = top_backport.change_branch
            else:
                chain_ref = self.base_tags[branch]
            chain_revision = self.localrepo.get_revision(chain_ref)
            upload_triggered = False

            # TODO: preventive backport, protected backports
            if protected_backports:
                reapply
            for index, item in enumerate(original_changes.iteritems()):
                uuid, change = item
                change.prepare_backport(self.replica_repo, replica_branch)
                if not upload_triggered:
                    equivalent_backport = self.localrepo.find_equivalent_commit(change.backport.pick_revision, chain_revision)
                    if equivalent_backport:
                        base_ref = equivalent_backport
                        log.info("Commit %s from upstream was already cherry-picked as %s in %s branch" % (change.backport.pick_revision, equivalent_backport, branch))
                        differ = self.localrepo.commits_differ(equivalent_backport, change.backport.pick_revision)
                        # commit already backported
                        backport_index = backports_list.index(equivalent_backport)
                        preventive_packports_list.remove(equivalent_backports)
                        log.debugvar('backport_index')
                        log.debugvar('index')
                        if backport_index == index:
                            log.info('backported commit is in the right order')
                        else:
                            log.info('backported commit is in different order')
                        if not differ:
                            log.info('backported commit is the same')
                        else:
                            log.info('backported commit content has changed')
                        # if backport_index != index or differ:
                        # different content is actually very difficult to control
                        # maybe it's better to remove diffcontrol lines from the content
                        if backport_index != index:
                            upload_triggered = True
                            log.info("UPLOAD TRIGGERED")
                            self.localrepo.create_base_pick_branch(replica_branch, base_ref)
                    else:
                        log.info('commit has not been backported yet')
                        upload_triggered = True
                        log.info("UPLOAD TRIGGERED")
                        self.localrepo.create_base_pick_branch(replica_branch, base_ref)

                if upload_triggered:
                    try:
                        change.backport.auto_attempt(replica_branch)
                    except CherryPickFailed, e:
                        log.critical("cherry pick failed")
                        change.backport.request_human_resolution(e)
                        failure_branch = "failed_attempts/%s" % target_branch
                        break

            for commit in preventive_backports:
                equivalent_change = self.original_repo.find_equivalent_change(commit)
                equivalent_change.prepare_backport(self.replica_repo, replica_branch)
                differ = self.localrepo.commits_differ(equivalent_change.revision, commit)
                if differ:
                    try:
                        equivalent_change.backport.auto_attempt(replica_branch)
                    except CherryPickFailed, e:
                        log.critical("cherry pick failed")
                        equivalent_change.backport.request_human_resolution(e)
                        failure_branch = "failed_attempts/%s" % target_branch
                        break


            if upload_triggered:
                latest_commit = self.localrepo.get_revision(replica_branch)
                topic = "update-to-commit-%s" % latest_commit
                try:
                    self.replica_repo.upload_change(replica_branch, replica_branch, topic)
                except UploadError:
                    log.crit("upload failed")
                    raise
