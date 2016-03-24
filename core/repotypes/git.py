import hashlib
import sys
import os
import re
from shellcommand import shell
from ..datastructures import Change
from gerrit import Gerrit
from ..colorlog import log, logsummary
from ..exceptions import CherryPickFailed, RemoteFetchError
from collections import OrderedDict


class Git(object):

    def __init__(self, directory):
        self.directory = directory
        self.remotes = dict()
        try:
            os.mkdir(self.directory)
        except OSError:
            pass
        os.chdir(self.directory)
        try:
            os.stat(".git")
        except OSError:
            shell('git init')

    def get_revision(self, ref):
        os.chdir(self.directory)
        # works with both tags and branches
        cmd = shell('git rev-list -n 1 %s' % ref)
        revision = cmd.output[0].rstrip('\n')
        return revision

    def addremote(self, repo, fetch=True):
        os.chdir(self.directory)
        cmd = shell('git remote | grep ^%s$' % repo.name)
        if cmd.returncode != 0:
            shell('git remote add %s %s' % (repo.name, repo.url))
        if fetch:
            cmd = shell('git fetch %s' % (repo.name))
            if cmd.returncode != 0:
                raise RemoteFetchError
        self.remotes[repo.name] = repo

    def fetch_changes(self, name):
        shell('git fetch %s +refs/changes/*:refs/remotes/%s/changes/*' % (name, name))

    def add_gerrit_remote(self, localrepo, name, location, project_name, fetch=True, fetch_changes=True):
        repo = Gerrit(localrepo, name, location, project_name)
        self.addremote(repo, fetch=fetch)
        repo.local_track = TrackedRepo(self, name, self.directory, project_name)
        if fetch_changes:
            self.fetch_changes(name)
        try:
            os.stat(".git/hooks/commit-msg")
        except OSError:
            shell('scp -p %s:hooks/commit-msg .git/hooks/' % location)

    def add_git_remote(self, localrepo, name, location, project_name, fetch=True):
        repo = RemoteGit(name, location, self.directory, project_name)
        self.addremote(repo, fetch=fetch)

    def list_branches(self, remote_name, pattern=''):
        os.chdir(self.directory)
        cmd = shell('git for-each-ref --format="%%(refname)" refs/remotes/%s/%s | sed -e "s/refs\/remotes\/%s\///"' % (remote_name, pattern, remote_name))
        return cmd.output

    def track_branch(self, branch, remote_branch):
        os.chdir(self.directory)
        shell('git checkout parking')
        shell('git branch --track %s %s' % (branch, remote_branch))

    def delete_branch(self, branch):
        os.chdir(self.directory)
        shell('git checkout parking')
        shell('git branch -D %s' % branch)

    def delete_remote_branches(self, remote_name, branches):
        os.chdir(self.directory)
        for branch in branches:
            shell('git push %s :%s' % (remote_name,branch))

    def get_commits(self, revision_start, revision_end, first_parent=True, reverse=True, no_merges=False):
        os.chdir(self.directory)
        options = ''
        commit_list = list()
        log.debug("Interval: %s..%s" % (revision_start, revision_end))

        os.chdir(self.directory)
        shell('git checkout parking')
        if reverse:
            options = '%s --reverse' % options
        if first_parent and not no_merges:
            options = '%s --first-parent' % options
        if no_merges:
            options = '%s --no-merges' % options
        cmd = shell('git rev-list %s --pretty="%%H" %s..%s | grep -v ^commit' % (options, revision_start, revision_end))

        for commit_hash in cmd.output:
            commit = dict()
            commit['hash'] = commit_hash
            cmd = shell('git show -s --pretty="%%P" %s' % commit_hash)
            commit['parents'] = cmd.output[0].split(' ')
            cmd = shell('git show -s --pretty="%%B" %s' % commit_hash)
            commit['body'] = cmd.output
            if len(commit['parents']) > 1:
                commit['subcommits'] = self.get_commits(commit['parents'][0], commit['parents'][1], first_parent=False, reverse=False)

            commit_list.append(commit)

        return commit_list

    def commits_differ(self, revision_a, revision_b):
        cmd =  shell('git show --pretty=format:"%%b" %s' % revision_a)
        body_a = '\n'.join(cmd.output)
        cmd =  shell('git show --pretty=format:"%%b" %s' % revision_b)
        body_b = '\n'.join(cmd.output)
        hash_a = hashlib.sha1(body_a).hexdigest()
        hash_b = hashlib.sha1(body_b).hexdigest()
        return hash_a != hash_b

    def find_latest_tag(self,branch):
        os.chdir(self.directory)
        cmd = shell('git rev-list %s' % branch, show_stdout=False)
        for revision in cmd.output:
            cmd = shell('git tag --points-at %s' % revision)
            if cmd.output:
                latest_tag = cmd.output[0]
                break
        return latest_tag

class LocalRepo(Git):

    def __init__(self, project_name, directory):
        super(LocalRepo, self).__init__(directory)
        self.project_name = project_name
        shell('git config diff.renames copy')
        shell('git config diff.renamelimit 10000')
        shell('git config merge.conflictstyle diff3')
        # TODO: remove all local branches
        # git for-each-ref --format="%(refname)" refs/heads | sed -e "s/refs\/heads//"
        # for branch in local_branches:
        #    shell('git branch -D %s' % branch)
        self.mirror_remote = None
        cmd = shell('git checkout parking')
        if cmd.returncode != 0:
            shell('git checkout --orphan parking')
            shell('git commit --allow-empty -a -m "parking"')

    def set_original(self, repo_type, location, project_name, fetch=True):
        self.original_type = repo_type
        if repo_type == 'gerrit':
            self.add_gerrit_remote(self, 'original', location, project_name, fetch=fetch, fetch_changes=False)
        elif repo_type == 'git':
            self.add_git_remote(self, 'original', location, project_name, fetch=fetch)
        else:
            log.critical('unknown original repo type')
            raise UnknownError
        self.original_remote = self.remotes['original']

    def set_replica(self, location, project_name, fetch=True):
        self.add_gerrit_remote(self, 'replica',  location, project_name, fetch=fetch, fetch_changes=fetch)
        self.replica_remote = self.remotes['replica']
        self.patches_remote = self.remotes['replica']

    def delete_service_branches(self):
        log.info("Deleting recomb branches from mirror for project %s" % self.project_name)
        service_branches = self.list_branches('replica', pattern='failed-cherrypicks/*')
        self.delete_remote_branches('replica', service_branches)

    def find_equivalent_commit(self, revision, branch):
        cmd = shell('git show -s --pretty=format:"%%an <%%ae>" %s' % revision)
        author = cmd.output[0]
        cmd = shell('git show -s --pretty=format:"%%at" %s' % revision)
        date = cmd.output[0]
        cmd = shell('git log --pretty=raw --author="%s" %s| grep -B 3 "%s" | grep commit\  | sed -e "s/commit //g"' % (author, branch, date))
        if cmd.output:
            return cmd.output[0]
        else:
            return None

    def add_conflicts_string(self, conflicts, commit_message):
        conflicts_string = "\nConflicts:\n  "
        conflicts_string = conflicts_string + '\n  '.join([x[3:] for x in conflicts])
        conflicts_string = conflicts_string + "\n\n"
        return re.sub('(Change-Id: .*\n)', '%s\g<1>' % (conflicts_string),commit_message)


    def create_base_pick_branch(self, branch, base_ref):
        os.chdir(self.directory)

        cmd = shell(' git rev-parse %s' % (base_ref))
        base_revision = cmd.output[0]

        cmd = shell('git branch --list %s' % branch)
        if cmd.output:
            cmd = shell('git branch -D %s' % branch)

        cmd = shell('git checkout -b %s %s' % (branch, base_revision))


    def cherrypick(self, branch, pick_revision, permanent_patches=None):

        os.chdir(self.directory)
        cmd = shell('git checkout %s' % (branch))
        cmd = shell('git cherry-pick %s' % (pick_revision))

        if cmd.returncode != 0:
            diffs = {}
            log.error("Cherry Pick Failed")
            status = ''
            cmd = shell('git status --porcelain')
            conflicts = cmd.output
            status = '\n    '.join([''] + conflicts)
            # TODO: add diff3 conflict blocks to output to status
            for filestatus in conflicts:
                filename = re.sub("^[A-Z]{1,2}\s+", "", filestatus) # re.sub('^[A-Z]*\ ', '')
                block_start = None
                block_end = None
                with open(filename) as conflict_file:
                    filecontent = conflict_file.read()
                for lineno, line in enumerate(filecontent.split('\n')):
                    rs = re.search('^<<<<<<<', line)
                    if rs is not None:
                        block_start = lineno
                    rs = re.search('^>>>>>>>', line)
                    if rs is not None:
                        block_end = lineno
                    if block_start is not None and block_end is not None:
                        block = '\n'.join(filecontent.split('\n')[block_start:block_end+1])
                        diffs[filename] = block
            cmd = shell('git cherry-pick --abort')
            raise CherryPickFailed(status, diffs)
        cmd = shell('git checkout parking')

    def remove_commits(self, branch, removed_commits, remote=''):
        shell('git branch --track %s%s %s' (remote, branch, branch))
        shell('git checkout %s' % branch)
        for commit in removed_commits:
            cmd = shell('git show -s %s' % commit)
            if cmd.output:
                shell('git rebase -p --onto %s^ %s' % (commit, commit))
                log.info('removed commit %s from branch %s' % (commit, branch))
            else:
                break
        if remote:
            shell('git push -f %s HEAD:%s' % (remote, branch))
            log.info('Pushed modified branch on remote')
        shell('git checkout parking')

class TrackedRepo(Git):

    def __init__(self, localrepo, name, directory, project_name):
        self.name = name
        self.directory = directory
        self.project_name = project_name
        self.localrepo = localrepo

    def get_changes(self, search_values, search_field='commit', results_key='revision', branch=None, raw_data=False, single_result=False):
        if type(search_values) is str or type(search_values) is unicode:
            search_values = [search_values]

        if search_field != 'commit':
            log.error('Tracked repo search does not support search by %s' % search_field)
            return None

        changes_data = OrderedDict()
        os.chdir(self.directory)
        for revision in search_values:
            infos = {}
            cmd = shell('git show -s --pretty=format:"%%H %%P" %s' % (revision))
            infos['id'] = cmd.output[0].split(' ')[0]
            infos['parents'] = cmd.output[0].split(' ')[1:]
            infos['revision'] = infos['id']
            if not branch:
                log.error("for git repositories you must specify a branch")
                sys.exit(1)
            else:
                infos['branch'] = branch
            infos['project-name'] = self.project_name
            changes_data[infos[results_key]] = infos

        if raw_data and not single_result:
            return changes_data
        if raw_data and single_result:
            if len(changes_data) == 1:
                return changes_data.popitem()[1]
            else:
                return None

        changes = OrderedDict()
        for key in changes_data:
            change = Change(remote=self, localrepo=self.localrepo)
            change.load_data(changes_data[key])
            changes[key] = change

        if single_result:
            if len(changes_data) == 1:
                return changes.popitem()[1]
            else:
                return None

        return changes

class RemoteGit(TrackedRepo):

    def __init__(self, localrepo, name, location, directory, project_name):
        super(RemoteGit, self).__init__(localrepo, name, directory, project_name)
        self.url = "git@%s:%s" % (location, project_name)

