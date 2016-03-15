import difflib
import hashlib
import sys
import os
import tempfile
import yaml
import shutil
import re
from ..utils import *
from shellcommand import shell
from ..datastructures import Change, EvolutionDiversityRecombination, OriginalDiversityRecombination, ReplicaMutationRecombination, Recombination
from gerrit import Gerrit
from ..colorlog import log, logsummary
from ..exceptions import RecombinationCanceledError, RecombinationFailed, RemoteFetchError
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

    def add_gerrit_remote(self, name, location, project_name, fetch=True, fetch_changes=True):
        repo = Gerrit(name, location, project_name)
        self.addremote(repo, fetch=fetch)
        repo.local_track = TrackedRepo(name, self.directory, project_name)
        if fetch_changes:
            shell('git fetch %s +refs/changes/*:refs/remotes/%s/changes/*' % (name, name))
        try:
            os.stat(".git/hooks/commit-msg")
        except OSError:
            shell('scp -p %s:hooks/commit-msg .git/hooks/' % location)

    def add_git_remote(self, name, location, project_name, fetch=True):
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
        if first_parent:
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

    def revision_exists(self, remote, revision, branch):
        cmd = shell("git ")
        return True

class Underlayer(Git):

    def __init__(self, project_name, directory):
        super(Underlayer, self).__init__(directory)
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
            self.add_gerrit_remote('original', location, project_name, fetch=fetch, fetch_changes=False)
        elif repo_type == 'git':
            self.add_git_remote('original', location, project_name, fetch=fetch)
        else:
            log.critical('unknow original repo type')
            raise UnknownError
        self.original_remote = self.remotes['original']

    def set_replica(self, location, project_name, fetch=True):
        self.add_gerrit_remote('replica',  location, project_name, fetch=fetch, fetch_changes=fetch)
        self.replica_remote = self.remotes['replica']
        self.recomb_remote = self.remotes['replica']
        self.patches_remote = self.remotes['replica']

    def delete_service_branches(self):
        if self.mirror_remote:
            log.info("Deleting recomb branches from mirror for project %s" % self.project_name)
            service_branches = self.list_branches('replica-mirror', pattern='recomb*')
            self.delete_remote_branches('replica-mirror', service_branches)
            service_branches = self.list_branches('replica-mirror', pattern='target-*')
            self.delete_remote_branches('replica-mirror', service_branches)
        else:
            log.info("No mirror repository specified for the project")

    def suggest_conflict_solution(self, recombination):
        patches_branch = recombination.patches_source.branch
        pick_revision = recombination.main_source.revision

        suggested_solution = None
        log.info("Trying to find a possible cause")
        cmd = shell('git show -s --pretty=format:"%%an <%%ae>" %s' % pick_revision)
        author = cmd.output[0]
        cmd = shell('git show -s --pretty=format:"%%at" %s' % pick_revision)
        date = cmd.output[0]
        cmd = shell('git log --pretty=raw --author="%s" | grep -B 3 "%s" | grep commit\  | sed -e "s/commit //g"' % (author, date))
        if cmd.output:
            suggested_solution = "Commit %s from upstream was already cherry-picked as %s in %s patches branch" % (pick_revision, cmd.output[0], patches_branch)

        return suggested_solution

    def add_conflicts_string(self, conflicts, commit_message):
        conflicts_string = "\nConflicts:\n  "
        conflicts_string = conflicts_string + '\n  '.join([x[3:] for x in conflicts])
        conflicts_string = conflicts_string + "\n\n"
        return re.sub('(Change-Id: .*\n)', '%s\g<1>' % (conflicts_string),commit_message)

    def cherrypick_recombine(self, recombination, permanent_patches=None):
        #shell('git fetch replica')
        #shell('git fetch original')

        pick_revision = recombination.main_source.revision
        merge_revision = recombination.patches_source.revision

        cmd = shell('git branch --list %s' % recombination.branch)
        if cmd.output:
            cmd = shell('git branch -D %s' % recombination.branch)

        cmd = shell('git branch -r --list replica/%s' % recombination.branch)
        if cmd.output:
            cmd = shell('git push replica :%s' % recombination.branch)

        cmd = shell('git checkout -b %s %s' % (recombination.branch, merge_revision))

        log.info("Creating remote disposable branch on replica")
        cmd = shell('git push replica HEAD:%s' % recombination.branch)

        cmd = shell('git cherry-pick --no-commit %s' % (pick_revision))
        # if merge fails, push empty change, and comment with git status.
        # TO FIND existing commit in patches (conflict resolution suggestions)
        # for commit in $(git rev-list --reverse --max-count 1000 --no-merges remotes/original/master); do AUTHOR=$(git show -s --pretty=format:"%an <%ae>" $commit); DATE=$(git show -s --pretty=format:"%at" $commit); CORRES=$(git log --pretty=raw --author="$AUTHOR" | grep -B 3 "$DATE" | grep commit\  | sed -e "s/commit //g"); if [ ! -z $CORRES ] ; then echo $commit in original/master is present in patches as $CORRES; fi; done
        if cmd.returncode != 0:
        #if cmd.returncode == 0:
            failure_cause = None
            log.error("Recombination Failed")
            cmd = shell('git status --porcelain')
            status = ''
            suggested_solution = ''
            try:
                if recombination.backport_change.exist_different:
                    pass
            except AttributeError:
                pass

            if failure_cause == "conflict":
                conflicts = cmd.output
                recombination.backport_change.commit_message = self.add_conflicts_string(conflicts, recombination.backport_change.commit_message)
                status = '\n    '.join([''] + conflicts)
                # TODO: add diff3 conflict blocks to output to status
                for filestatus in conflicts:
                    filename = filestatus[2:] # re.sub('^[A-Z]*\ ', '')
                    with open(filename) as conflict_file:
                        filecontent = conflict_file.read()
                    for lineno, line in enum(filecontent.split('\n')):
                        rs = re.search('^<<<<<<', line)
                        if rs is not None:
                            block_start = line
                        rs = re.search('^>>>>>>', line)
                        if rs is not None:
                            block_end = line
                    block = '\n'.join(filecontent.split('\n')[block_start:block_end])
                diffs[filename] = block
                suggested_solution = self.suggest_conflict_solution(recombination)
            cmd = shell('git cherry-pick --abort')
            recombination.status = "FAILED"
            self.commit_recomb(recombination)
            raise RecombinationFailed(status, suggested_solution)
        else:
            recombination.status = "SUCCESSFUL"
            self.commit_recomb(recombination)

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

    def get_original_ids(self, commits):
        ids = OrderedDict()
        for commit in commits:
            if self.original_type == 'gerrit':
                main_revision = commit['hash']
                # in gerrit, merge commits do not have Change-id
                # if commit is a merge commit, search the second parent for a Change-id
                if len(commit['parents']) != 1:
                    commit = commit['subcommits'][0]
                found = False
                for line in commit['body']:
                    # if more than one Change-Id line is found, use only the last
                    if re.search('Change-Id: ', line):
                        change_id = re.sub(r'\s*Change-Id: ', '', line)
                        found = True
                if found:
                    ids[change_id] = main_revision
                else:
                    log.warning("no Change-id found in commit %s or its ancestors" % main_revision)

            elif self.original_type == 'git':
                ids[commit['hash']] = commit['hash']

        return ids

        mutation_change = self.patches_remote.get_changes_by_id([patches_change_id])[patches_change_id]
        patches_branch = mutation_change.branch

        return recombination


                # Set real commit as revision
                original_changes[change_id].revision = original_ids[change_id]
                if replication_strategy == "lock-and-backports":
                    lock_revision = self.get_revision(replica_lock)
                    cmd = shell('git show -s --pretty=format:"%%an <%%ae>" %s' % original_ids[change_id])
                    author = cmd.output[0]
                    cmd = shell('git show -s --pretty=format:"%%at" %s' % original_ids[change_id])
                    date = cmd.output[0]
                    cmd = shell('git log --pretty=raw --author="%s" %s..%s | grep -B 3 "%s" | grep commit\  | sed -e "s/commit //g"' % (author, lock_revision, diversity_revision, date))
                    if cmd.output:
                        backport_change = self.patches_remote.get_change(cmd.output[0], search_field='commit')
                        # TODO: evaluate body diff.
                        # if body_diff:
                        #     log.warning ('backport is present but patch differs')
                        #     backport_change.exist_different = True
                    else:
                        backport_change = Change(remote=self.patches_remote)
                    # backport_change.branch = self.underlayer.branch_maps['patches']['original'][self.evolution_change.branch]
                    recombination.initialize(self.recomb_remote, evolution_change=original_changes[change_id], diversity_change=diversity_change, backport_change=backport_change)
                elif replication_strategy == "change-by-change":
                    recombination.initialize(self.recomb_remote, original_change=original_changes[change_id], diversity_change=diversity_change)


class TrackedRepo(Git):

    def __init__(self, name, directory, project_name):
        self.name = name
        self.directory = directory
        self.project_name = project_name

    def get_changes_data(self, search_values, search_field='commit', results_key='revision', branch=None):
        if type(search_values) is str or type(search_values) is unicode:
            search_values = [search_values]

        if search_field != 'commit':
            log.error('Tracked repo search does not support search by %s' % search_field)
            return None

        changes_data = dict()
        os.chdir(self.directory)
        for revision in search_values:
            infos = {}
            cmd = shell('git show -s --pretty=format:"%%H %%P" %s' % (revision))
            infos['id'], infos['parent'] = cmd.output[0].split(' ')[0:2]
            infos['revision'] = infos['id']
            if not branch:
                log.error("for git repositories you must specify a branch")
                sys.exit(1)
            else:
                infos['branch'] = branch
            infos['project-name'] = self.project_name
            changes_data[infos[results_key]] = infos

        return changes_data

    def get_change_data(self, search_value, search_field='commit', results_key='revision', branch=None):
        change_data = self.get_changes_data(search_value, search_field=search_field, results_key=results_key, branch=branch)

        if len(change_data) == 1:
            change_data = change_data.popitem()[1]
        else:
            return None

        return change_data

    def get_changes(self, search_values, search_field='commit', results_key='revision', branch=None, search_merged=True):
        changes_data = self.get_changes_data(search_values, search_field=search_field, results_key=results_key, branch=branch)

        changes = OrderedDict()
        for key in changes_data:
            change = Change(remote=self)
            change.load_data(changes_data[key])
            changes[key] = change
        return changes

    def get_change(self, search_values, search_field='commit', results_key='revision', branch=None):
        change_data = self.get_changes(search_values, search_field=search_field, results_key=results_key, branch=branch)

        if len(change_data) == 1:
            change = change_data.popitem()[1]
        else:
            return None

        return change

class RemoteGit(TrackedRepo):

    def __init__(self, name, location, directory, project_name):
        super(RemoteGit, self).__init__(name, directory, project_name)
        self.url = "git@%s:%s" % (location, project_name)

