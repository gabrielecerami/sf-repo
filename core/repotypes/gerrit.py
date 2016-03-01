# Performs sanity check for midstream
import json
import pprint
from ..colorlog import log
from shellcommand import shell
from ..datastructures import Change
from collections import OrderedDict


class Gerrit(object):

    def __init__(self, name, host, project_name):
        self.host = host
        self.name = name
        self.project_name = project_name
        self.url = "ssh://%s/%s" % (host, project_name)

    def query_changes_json(self, query, comments=False):
        changes_infos = list()
        cmd = shell('ssh %s gerrit query --comments --current-patch-set --format json %s' % (self.host,query))
        log.debug(pprint.pformat(cmd.output))
        for change_json in cmd.output:
            if change_json !='':
                change = json.loads(change_json)
                if "type" not in change or (change['type'] != 'stats' and change['type'] != 'error'):
                    changes_infos.append(change)

        log.debug("end query json")
        return changes_infos

    def approve_change(self, number, patchset):
        shell('ssh %s gerrit review --code-review 2 --verified 1 %s,%s' % (self.host, number, patchset))

    def reject_change(self, number, patchset):
        shell('ssh %s gerrit review --code-review -2 --verified -1 %s,%s' % (self.host, number, patchset))

    def submit_change(self, number, patchset):
        shell('ssh %s gerrit review --publish --project %s %s,%s' % (self.host, self.project_name, number, patchset))
        shell('ssh %s gerrit review --submit --project %s %s,%s' % (self.host, self.project_name, number, patchset))
        cmd = shell('ssh %s gerrit query --format json "change:%s AND status:merged"' % (self.host, number))
        if cmd.output[:-1]:
            return True
        return False

    def publish_change(self, number, patchset):
        shell('ssh %s gerrit review --publish --project %s %s,%s' % (self.host, self.project_name, number, patchset))

    def abandon_change(self, number, patchset):
        shell('ssh %s gerrit review --abandon --project %s %s,%s' % (self.host, self.project_name, number, patchset))

    def upload_change(self, branch, topic, reviewers=None, successremove=True):
        command = 'git push %s HEAD:refs/drafts/%s/%s' % (self.name, branch, topic)
        if reviewers:
            command = "%s%%" % command
            for reviewer in reviewers:
                command = command + "r=%s," % reviewer
            command.rstrip(',')

        # FIXME: check upload results in another way
        shell('git checkout %s' % branch)
        #cmd = shell('git review -D -r %s -t "%s" %s' % (self.name, topic, branch))
        #for line in cmd.output:
        #    if 'Nothing to do' in line:
        #        log.debug("trying alternative upload method")
        #        shell("git push %s HEAD:refs/drafts/%s/%s" % (self.name, branch, topic))
        #        break
        shell(command)
        cmd = shell('ssh %s gerrit query --current-patch-set --format json "topic:%s AND status:open"' % (self.host, topic))
        shell('git checkout parking')
        log.debug(pprint.pformat(cmd.output))
        if not cmd.output[:-1] and successremove:
            shell('git push replica :%s' % branch)
            return None
        gerrit_infos = json.loads(cmd.output[:-1][0])
        infos = self.normalize_infos(gerrit_infos)
        return infos

    def comment_change(self, number, patchset, comment_message, verified=None, code_review=None):
        review_input = dict()
        review_input['labels'] = dict()
        review_input['message'] = comment_message
        if code_review:
            review_input['labels']['Code-Review'] = code_review
        if verified:
            review_input['labels']['Verified'] = verified

        json_input = json.dumps(review_input, ensure_ascii=False)

        cmd = shell("echo '%s' | ssh %s gerrit review --json %s,%s" % (json_input, self.host, number, patchset))

    def get_query_string(self, criteria, ids, branch=None, search_merged=True):
        query_string = '\(%s:%s' % (criteria, ids[0])
        for change in ids[1:]:
            query_string = query_string + " OR %s:%s" % (criteria,change)
#        uncomment this below and remove the if else block
        query_string = query_string + "\) AND project:%s AND NOT status:abandoned" % (self.project_name)
        if not search_merged:
            query_string = query_string + " AND NOT status:merged"
#        if self.name == 'original':
#            query_string = query_string + "\) AND project:openstack/nova AND NOT status:abandoned"
#        elif criteria == "commit":
#            query_string = query_string + "\) AND project:nova AND NOT status:abandoned"
#        elif criteria == "topic":
#            query_string = query_string + "\) AND project:nova-gitnetics AND NOT status:abandoned"
#        elif criteria == "change":
#            query_string = query_string + "\) AND \(project:nova OR project:nova-gitnetics\) AND NOT status:abandoned"


        if branch:
            query_string = query_string + " AND branch:%s " % branch
        log.debug("search in %s gerrit: %s" % (self.name, query_string))
        return query_string

    def normalize_infos(self, gerrit_infos):
        infos = {}
        infos['revision'] = gerrit_infos['currentPatchSet']['revision']
        infos['parent'] = gerrit_infos['currentPatchSet']['parents'][0]
        infos['patchset_number'] = gerrit_infos['currentPatchSet']['number']
        infos['patchset_revision'] = gerrit_infos['currentPatchSet']['revision']
        infos['project-name'] = gerrit_infos['project']
        infos['branch'] = gerrit_infos['branch']
        infos['id'] = gerrit_infos['id']
        infos['previous-commit'] = infos['parent']
        if 'topic' in gerrit_infos:
            infos['topic'] = gerrit_infos['topic']
        infos['number'] = gerrit_infos['number']
        infos['status'] = gerrit_infos['status']
        infos['url'] = gerrit_infos['url']
        infos['comments'] = None
        if 'comments' in gerrit_infos:
            infos['comments'] = gerrit_infos['comments']
        infos['commit-message'] = gerrit_infos['commitMessage']

        infos['approvals'] = dict()
        if 'approvals' in gerrit_infos['currentPatchSet']:
            code_review = -2
            verified = -1
            for patchset_approval in gerrit_infos['currentPatchSet']['approvals']:
                if patchset_approval['type'] == 'Code-Review':
                    code_review = max(code_review, int(patchset_approval['value']))
                if patchset_approval['type'] == 'Verified':
                    verified = max(verified, int(patchset_approval['value']))
        else:
            code_review = 0
            verified = 0
        infos['approvals']['code-review'] = code_review
        infos['approvals']['verified'] = verified

        return infos

    def get_changes_data(self, search_values, search_field='change', results_key='id', branch=None, sort_key='number', search_merged=True):
        if type(search_values) is str or type(search_values) is unicode:
            search_values = [search_values]

        query_string = self.get_query_string(search_field, search_values, branch=branch, search_merged=search_merged)
        changes_data = self.query_changes_json(query_string)

        changes_data.sort(key=lambda data: data[sort_key])
        log.debugvar('changes_data')
        data = OrderedDict()
        for gerrit_data in changes_data:
            norm_data = self.normalize_infos(gerrit_data)
            data[norm_data[results_key]] = norm_data

        # fallback to local tracked repo
        if not data and search_field == 'change' and branch is not None:
            try:
                data = self.local_track.get_changes_data(search_values, branch=branch)
            except AttributeError:
                pass

        return data

    def get_change_data(self, search_value, search_field='change', results_key='id', branch=None):
        change_data = self.get_changes_data(search_value, search_field=search_field, results_key=results_key, branch=branch)

        if len(change_data) == 1:
            change_data = change_data.popitem()[1]
        else:
            return None

        return change_data

    def get_changes(self, search_values, search_field='change', results_key='id', branch=None, search_merged=True):
        change_data = self.get_changes_data(search_values, search_field=search_field, results_key=results_key, branch=branch, search_merged=search_merged)

        changes = OrderedDict()
        for key in change_data:
            change = Change(remote=self)
            change.load_data(change_data[key])
            changes[key] = change

        return changes

    def get_change(self, search_values, search_field='change', results_key='id', branch=None):
        change_data = self.get_changes(search_values, search_field=search_field, results_key=results_key, branch=branch)

        if len(change_data) == 1:
            change = change_data.popitem()[1]
        else:
            return None

        return change

    def get_untested_recombs_infos(self, recomb_id=None, branch=''):
        if recomb_id:
            change_query = 'AND change:%s' % recomb_id
        else:
            change_query = ''
        query = "'owner:self AND project:%s %s AND branch:^recomb-.*-%s.* AND ( NOT label:Code-Review+2 AND NOT label:Verified+1 AND status:open)'"  % (self.project_name, change_query, branch)
#        query = "'owner:self AND project:nova-gitnetics %s AND branch:^recomb-.*-%s.* AND ( NOT label:Code-Review+2 AND NOT label:Verified+1 AND NOT status:abandoned)'"  % (change_query, branch)
        untested_recombs = self.query_changes_json(query)
        log.debugvar('untested_recombs')
        return untested_recombs

    def get_approved_change_infos(self, branch):
        infos = dict()
#        query_string = "'owner:self AND project:%s AND branch:^%s AND label:Code-Review+2 AND label:Verified+1 AND NOT status:abandoned'" % (self.project_name, branch)
#        query_string = "'owner:self AND project:nova AND branch:^%s AND label:Code-Review+2 AND label:Verified+1 AND NOT status:abandoned'" (branch)
        changes_infos = self.query_changes_json(query_string)

        for gerrit_infos in changes_infos:
            norm_infos = self.normalize_infos(gerrit_infos)
            infos[norm_infos['number']] = norm_infos

        return infos

