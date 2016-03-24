import json
import pprint
from ..colorlog import log
from shellcommand import shell
from ..datastructures import Change
from collections import OrderedDict


class Gerrit(object):

    def __init__(self, localrepo, name, host, project_name):
        self.host = host
        self.name = name
        self.project_name = project_name
        self.url = "ssh://%s/%s" % (host, project_name)
        self.localrepo = localrepo

    def query_changes_json(self, query, comments=False):
        changes_infos = list()
        cmd = shell('ssh %s gerrit query --comments --current-patch-set --format json --dependencies --submit-records %s' % (self.host,query))
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

    def upload_change(self, source_branch, target_branch, topic, reviewers=None):
        command = 'git push %s %s:refs/drafts/%s/%s' % (self.name, source_branch, target_branch, topic)
        if reviewers:
            command = "%s%%" % command
            for reviewer in reviewers:
                command = command + "r=%s," % reviewer
            command.rstrip(',')

        # FIXME: check upload results in another way
        #cmd = shell('git review -D -r %s -t "%s" %s' % (self.name, topic, branch))
        #for line in cmd.output:
        #    if 'Nothing to do' in line:
        #        log.debug("trying alternative upload method")
        #        shell("git push %s HEAD:refs/drafts/%s/%s" % (self.name, branch, topic))
        #        break
        shell(command)
        log.debug(command)
        cmd = shell('git branch -D %s' % source_branch)
        cmd = shell('ssh %s gerrit query --current-patch-set --format json "topic:%s AND status:open"' % (self.host, topic))
        if not cmd.output[:-1]:
            return None
        gerrit_infos = json.loads(cmd.output[:-1][0])
        infos = self.normalize_infos(gerrit_infos)
        return infos

    def get_blocked_changes(self):
        #self.get_changes(branch="^failed_attempts/.*")
        return None

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
        query_string = query_string + "\) AND project:%s AND NOT status:abandoned" % (self.project_name)
        if not search_merged:
            query_string = query_string + " AND NOT status:merged"

        if branch:
            query_string = query_string + " AND branch:%s " % branch
        log.debug("search in %s gerrit: %s" % (self.name, query_string))
        return query_string

    def normalize_infos(self, gerrit_infos):
        patchset = gerrit_infos.pop('currentPatchSet')
        gerrit_infos['revision'] = patchset['revision']
        gerrit_infos['parent'] = patchset['parents'][0]
        gerrit_infos['patchset_number'] = patchset['number']
        gerrit_infos['patchset_revision'] = patchset['revision']

        gerrit_infos['previous_commit'] = gerrit_infos['parent']
        if 'comments' not in gerrit_infos:
            gerrit_infos['comments'] = None
        gerrit_infos['change_branch'] = "%s/changes/%s/%s/%s" % (self.name, gerrit_infos['number'][-2:], gerrit_infos['number'], gerrit_infos['patchset_number'])

        gerrit_infos['approvals'] = dict()
        if 'approvals' in patchset:
            code_review = -2
            verified = -1
            for patchset_approval in patchset['approvals']:
                if patchset_approval['type'] == 'Code-Review':
                    code_review = max(code_review, int(patchset_approval['value']))
                if patchset_approval['type'] == 'Verified':
                    verified = max(verified, int(patchset_approval['value']))
        else:
            code_review = 0
            verified = 0
        gerrit_infos['approvals']['code-review'] = code_review
        gerrit_infos['approvals']['verified'] = verified

        return gerrit_infos

    def get_changes(self, search_values=None, search_field='change', results_key='id', sort_key='number', branch=None, search_merged=True, single_result=False, raw_data=False, chain=False):
        #query_string = self.get_query_string(search_field, search_values, branch=branch, search_merged=search_merged)
        query_string = "project:%s AND branch:%s AND status:open" % (self.project_name, branch)
        changes_data = self.query_changes_json(query_string)

        if single_result and len(changes_data) != 1:
            return None
        changes_data.sort(key=lambda data: data[sort_key])
        results = OrderedDict()
        tmp_chain = list()
        results_chain = OrderedDict()
        top_of_chain = None

        for gerrit_data in changes_data:
            norm_data = self.normalize_infos(gerrit_data)
            if raw_data:
                results[gerrit_data[results_key]] = norm_data
            else:
                change = Change(remote=self, infos=norm_data)
                results[gerrit_data[results_key]] = change
                if 'neededBy' not in norm_data:
                    top_of_chain = change

        if single_result:
           results = results.popitem()[1]
        if chain and top_of_chain:
            change_id = top_of_chain.revision
            while change_id in results:
                change = results[change_id]
                tmp_chain.insert(0, change)
                if hasattr(change, 'dependsOn'):
                    tmp_chain.insert(0, change)
                    change_id = change.dependsOn[0][results_key]
                else:
                    change_id = None



            for change in tmp_chain:
                results_chain[change.revision] = change

            return results_chain

        return results
