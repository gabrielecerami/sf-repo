import yaml
import sys
import re
import argparse
import os
from core.colorlog import log,logsummary
from core.repos import Repos


def projectname(project_name):
    return re.sub('.*/', '', project_name)

def dump(data, path):
    with open(path, "w") as dump_file:
        yaml.safe_dump(data, stream=dump_file, explicit_start=True, default_flow_style=False, indent=4, canonical=False, default_style=False)


def parse_args(parser):
    # common arguments
    parser.add_argument('--projects-conf', '-f', dest='projects_path', type=argparse.FileType('r'), required=True,  help='path of the projects.yaml file')
    parser.add_argument('--base-dir','-d', dest='base_dir', action='store', required=True, help='base dir for local repos')
    parser.add_argument('--projects','-p', dest='projects', action='store', type=projectname, help='comma separated list of project')
    parser.add_argument('-m', '--watch-method', dest='watch_method', action='store', help='upstream branch to consider')
    parser.add_argument('-w', '--watch-branches', dest='watch_branches', action='store', help='upstream branch to consider')
    parser.add_argument('--no-fetch', dest='fetch', action='store_false', help='upstream branch to consider')

    subparsers = parser.add_subparsers(dest='command')

    parser_new_original_change = subparsers.add_parser('poll')
    parser_new_original_change.add_argument('-b', '--original-branch', dest='original_branch', action='store',  help='upstream branch to consider')

    args = parser.parse_args()

    return args


if __name__=="__main__":

    parser = argparse.ArgumentParser(description='Map the git out of upstream')
    args = parse_args(parser)
    log.debugvar('args')

    projects = yaml.load(args.projects_path.read())
    try:
        repos = Repos(projects, args.base_dir, filter_projects=args.projects, filter_method=args.watch_method, filter_branches=args.watch_branches, fetch=args.fetch)
    except ValueError:
        log.critical('No projects to handle')
        sys.exit(1)

    ## actions

    if args.command == 'poll':
        repos.poll()

