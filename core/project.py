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

        self.localrepo = Underlayer(project_name, local_dir)

        # Set up remotes
        self.localrepo.set_replica(self.replica_project['location'], self.replica_project['name'], fetch=fetch)
        self.localrepo.set_original(self.original_project['type'], self.original_project['location'], self.original_project['name'], fetch=fetch)

        # Set up branches hypermap
        # get branches from original
        # self.original_branches = self.underlayer.list_branches('original')
        self.branches = project_info['original']['watch-branches']

        for branch in branches:
            if not hasattr(brach, 'replica-branch');
                branch['replica-branch'] = branch[name]


    def fetch_changes_chain(branch):
        pass

    def scan_original_distance(self, original_branch):
        replica_branch = self.underlayer.branch_maps['original->replica'][original_branch]
        log.debug("Scanning distance from original branch %s" % original_branch)

        # change OrderedDict
        changes = get_cmmitst()
        for change in changes:
            test_change

