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
        self.wedgeports_count = project_info['replica']['wedgeports-count']

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
            self.poll_branch(branch)

    def poll_branch(self, branch):
        replica_branch = self.branches[branch]['replica-branch']
        original_branch = 'remotes/original/' + branch
        commits_fromtag = self.localrepo.get_commits(self.base_tags[branch], original_branch)
        if not commits_fromtag:
            log.info("No new commits in branch")
            return None

        blocked_changes = self.localrepo.replica_remote.get_blocked_changes()
        if blocked_changes:
            log.info("there are blocked changes that must be solved before continuing")
            return False

        base_ref = self.base_tags[branch]
        base_branch_name = replica_branch + "/" + base_ref
        base_branch = self.localrepo.create_branch(base_branch_name, base_ref)
        original_changes = self.original_repo.local_track.get_changes([commit['hash'] for commit in commits_fromtag], branch=original_branch)
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
        ports = self.replica_repo.get_changes(branch=replica_branch, chain=True, results_key='revision')
        ports_list = list(backports)
        wedgeports = ports_list[:self.wedgeports_count]
        if wedgeports:
            base_ref = wedgeports[-1]
        backports = ports_list[self.wedgeports_count:]
        forwardports = set(backports)
        if ports:
            tb_id, top_port = ports.popitem(last=True)
            chain_ref = top_port.change_branch
        else:
            chain_ref = self.base_tags[branch]
        chain_revision = self.localrepo.get_revision(chain_ref)
        upload_triggered = False
        self.scan_ports(original_changes, wedgeports_count, chain_revision)

    def scan_ports(self, original_changews):
        # TODO: preventive backport, protected backports
        l = []
        for index, item in enumerate(original_changes.iteritems()):
            uuid, change = item
            e = {}
            change.prepare_backport(self.replica_repo, replica_branch)
            e['change'] = change
            port = self.localrepo.find_equivalent_commit(change.backport.pick_revision, chain_revision)
            e['port'] = port
            e['contents-differ'] = True
            e['port-index'] = -1
            e['index-differs'] = True
            if port:
                log.info("Commit %s from upstream was already cherry-picked as %s in %s branch" % (change.backport.pick_revision, equivalent_backport, branch))

                e['contents-differ'] = self.localrepo.commits_differ(port, change.backport.pick_revision)
                if e['contents-differ']:
                    log.info('backported commit content has changed')
                else:
                    e['contents-differ'] = False
                    log.info('backported commit is the same')

                e['port-index'] = backports_list.index(port)
                if e['port-index'] == index:
                    log.info('backported commit is in the right order')
                else:
                    e['index-differs'] = False
                    log.info('backported commit is in different order')

                forwardports.remove(port)
            else:
                log.info("Commit %s from upstream is not present in %s branch" % (change.backport.pick_revision, branch))

            l.append(e)

        for fp_index, commit in enumerate(forwardports):
            e = {}
            change = self.original_repo.find_equivalent_change(commit)
            change.prepare_backport(self.replica_repo, replica_branch)
            e['change'] = change
            e['contents-differ'] = self.localrepo.commits_differ(equivalent_change.revision, commit)
            e['port-index'] = index + fp_index
            e['index-differs'] = False
            l.append(e)

        for index, e in l:
            if e['port']:
                # commit already backported
                # if backport_index != index or differ:
                # different content is actually very difficult to control
                # maybe it's better to remove diffcontrol lines from the content
                if e['index-differs'] or e['contents-differ']:
                    reupload_chain = True
                    break
                else:
                    base_ref = e['port']
            else:
                reupload_chain = True
                break

        if reupload_chain:
            self.localrepo.create_branch(replica_branch, base_ref)

            for e in l[index:]:
                change = e['change']
                try:
                    change.backport.auto_attempt(replica_branch)
                except CherryPickFailed, e:
                    log.critical("cherry pick failed")
                    change.backport.request_human_resolution(e)
                    failure_branch = "failed_attempts/%s" % target_branch
                    raise

            latest_commit = self.localrepo.get_revision(replica_branch)
            topic = "update-to-commit-%s" % latest_commit
            try:
                self.replica_repo.upload_change(replica_branch, replica_branch, topic)
            except UploadError:
                log.crit("upload failed")
                raise
