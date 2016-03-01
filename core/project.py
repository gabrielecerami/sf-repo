import pprint
import re
import copy
import os
import yaml
from colorlog import log, logsummary
from collections import OrderedDict
from repotypes.git import Underlayer
from exceptions import *


class Project(object):

    status_impact = {
        "UPLOADED": 1,
        "MISSING": 0
    }

    def __init__(self, project_name, project_info, local_dir, fetch=True):
        self.project_name = project_name
        self.commits = dict()

        log.info('Current project:\n' + pprint.pformat(project_info))
        self.original_project = project_info['original']
        self.replica_project = project_info['replica']
        self.rev_deps = None
        if 'rev-deps' in project_info:
            self.rev_deps = project_info['rev-deps']

        self.patches_branch_suffix = "-patches"
        # Set up local repo
        self.underlayer = Underlayer(project_name, local_dir)

        # Set up remotes
        self.underlayer.set_replica(self.replica_project['location'], self.replica_project['name'], fetch=fetch)
        self.underlayer.set_original(self.original_project['type'], self.original_project['location'], self.original_project['name'], fetch=fetch)

        # Set up branches hypermap
        # get branches from original
        # self.original_branches = self.underlayer.list_branches('original')
        self.original_branches = project_info['original']['watch-branches']
        self.backports_startref = dict()
        for original_branch in self.original_branches:
            if 'backports-start' in  self.original_project:
                self.backports_startref[original_branch] = self.original_project['backports-start'][original_branch]
            # apply mapping to find target branch
            try:
                replica_branch = project_info['replica']['branch-mappings'][original_branch]
            except KeyError:
                replica_branch = original_branch
            target_branch = '%s%s' % (replica_branch, self.target_branch_suffix)
            patches_branch = '%s%s' % (replica_branch, self.patches_branch_suffix)
            self.underlayer.set_branch_maps(original_branch, replica_branch, target_branch, patches_branch)

            self.recombinations[replica_branch] = None
            self.commits[replica_branch] = {}

        self.ref_locks = dict()
        if 'ref-locks' in self.replica_project:
            for branch in self.replica_project['ref-locks']:
                # no advancement will be performed past this revision on this branch
                self.ref_locks[branch] = self.replica_project['ref-locks'][branch]

    def scan_original_distance(self, original_branch):
        replica_branch = self.underlayer.branch_maps['original->replica'][original_branch]
        log.debug("Scanning distance from original branch %s" % original_branch)

        self.recombinations[original_branch] = self.get_recombinations_by_interval(original_branch)
        slices = self.get_slices(self.recombinations[original_branch])
        recombinations = self.recombinations[original_branch]


        log.debugvar('slices')
        # Master sync on merged changes
        # we really need only the last commit in the slice
        # we advance the master to that, and all the others will be merged too
        if slices['UPLOADED']:
            # one or more changes are merged in midstream, but missing in master
            # master is out of sync, changes need to be pushed
            # but check first if the change was changed with a merge commit
            # if yes, push THAT to master, if not, it's just a fast forward
            segment = slices['MERGED'][0]
            recomb_id = list(recombinations)[segment['end'] - 1]
            recombination = recombinations[recomb_id]
            recombination.handle_status()

        # Gerrit operations from approved changes
        # NOthing 'approved' can be merged if it has some "present" before in the history
        skip_list = set()
        for index, approved_segment in enumerate(slices['APPROVED']):
            for present_segment in slices['PRESENT']:
                if present_segment['start'] < approved_segment['start']:
                    skip_list.add(index)

        # Notify of presence
        for segment in slices['PRESENT']:
            for recomb_id in list(recombinations)[segment['start']:segment['end']]:
                recombination = recombinations[recomb_id]
                log.warning("Recombination %s already present in replica gerrit as change %s and waiting for approval" % (recomb_id, recombination.number))
                recombination.handle_status()

        # Gerrit operations for missing changes
        for segment in slices['MISSING']:
            for recomb_id in list(recombinations)[segment['start']:segment['end']]:
                log.warning("Recombination %s is missing from replica gerrit" % recomb_id)
                recombination = recombinations[recomb_id]
                recombination.handle_status()

        return True

    def poll_original_branches(self):
        for branch in self.original_branches:
            self.scan_original_distance(branch)

    def get_recombinations_by_interval(self, original_branch):
        ref_end = 'original/%s' % (original_branch)
        replica_branch = self.underlayer.branch_maps['original->replica'][original_branch]
        patches_branch = self.underlayer.branch_maps['original->patches'][original_branch]

        if original_branch in self.backports_startref:
            ref_start = self.backports_startref[original_branch]
        else:
            ref_start = self.ref_locks[replica_branch]
        self.commits[replica_branch] = self.underlayer.get_commits(ref_start, ref_end, first_parent=False, no_merges=True)

        commits = self.commits[replica_branch]

        original_ids = self.underlayer.get_original_ids(commits)

        replica_lock = None
        if replica_branch in self.ref_locks:
            replica_lock = self.ref_locks[replica_branch]

        if original_ids:
            recombinations = self.underlayer.get_recombinations_from_original(original_branch, original_ids, diversity_refname, self.replication_strategy, replica_lock)
            return recombinations
        return None


    def get_reverse_dependencies(self, tags=[]):
        rev_deps = dict()
        for project in self.rev_deps:
            for tag in tags:
                if tag in self.rev_deps[project]["tags"]:
                    rev_deps[project] = self.rev_deps[project]["tests"]
                    break
        return rev_deps


    def vote_recombinations(self, test_results, recomb_id=None):
        if recomb_id:
            recombs = [recomb_id]
        else:
            recombs = [recomb for recomb in test_results]

        for recomb_id in recombs:
            recombination = self.underlayer.get_recombination(recomb_id)
            test_score, test_analysis = self.get_test_score(test_results[recomb_id])
            if test_score > self.test_minimum_score:
                if self.replication_strategy == "lock-and-backports":
                    comment_data = dict()
                    comment_data['backport-test-results'] = dict()
                    build_url = os.environ.get('BUILD_URL')
                    if build_url:
                        comment_data['backport-test-results']['message'] = "test-link: %s" % build_url
                    else:
                        comment_data['backport-test-results']['message'] = ""
                    comment_data['backport-test-results']['Code-Review'] = 0
                    comment_data['backport-test-results']['Verified'] = "1"
                    comment_data['backport-test-results']['reviewers'] = self.replica_project['success_reviewers_list']
                    comment = yaml.dump(comment_data)
                    recombination.comment(comment)
                recombination.approve()
                logsummary.info("Recombination %s Approved" % recomb_id)
            else:
                recombination.reject()
                logsummary.info("Recombination %s Rejected: %s" % (recomb_id, test_analysis))

