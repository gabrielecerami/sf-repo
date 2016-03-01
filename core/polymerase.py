import copy
import traceback
from colorlog import log, logsummary
from project import Project
import sys


class Repowatch(object):

    def __init__(self, projects_conf, base_dir, filter_projects=None, filter_method=None, filter_branches=None, fetch=True):
        self.projects = dict()
        self.projects_conf = projects_conf
        self.base_dir = base_dir
        # extract reverse dependencies
        for project in self.projects_conf:
            self.projects_conf[project]["rev-deps"] = {}
        for project in self.projects_conf:
            if "test-teps" in self.projects_conf[project]:
                for test_dep in self.projects_conf[project]["test-deps"]:
                    rev_dep = {
                        project : {
                        "tags" :self.projects_conf[project]["test-deps"][test_dep],
                        "tests":self.projects_conf[project]["replica"]["tests"]
                        }
                    }
                    self.projects_conf[test_dep]["rev-deps"].update(rev_dep)

        # restrict project to operate on
        projects = copy.deepcopy(projects_conf)
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

        for project_name in projects:
            try:
                self.projects[project_name] = Project(project_name, projects[project_name], self.base_dir + "/"+ project_name, fetch=fetch)
                logsummary.info("Project: %s initialized" % project_name)
            except Exception, e:
                traceback.print_exc(file=sys.stdout)
                log.error(e)
                logsummary.error("Project %s skipped, reason: %s" % (project_name, e))

    def poll_original(self):
        logsummary.info('Polling original for new changes. Checking status of all changes.')
        for project_name in self.projects:
            try:
                logsummary.info('Polling project: %s' % project_name)
                project = self.projects[project_name]
                project.poll_original_branches()
            except Exception, e:
                traceback.print_exc(file=sys.stdout)
                log.error(e)
                logsummary.error("Project %s skipped, reason: %s" % (project_name, e))
