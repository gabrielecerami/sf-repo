import yaml
import re
from colorlog import log
from exceptions import *

class Change(object):

    def __init__(self, remote=None, infos=None, localrepo=None):
        if infos:
            self.load_data(infos)
        else:
            self.branch = None
            self.topic = None

        self.remote = None
        if remote:
            self.remote = remote
        self.remote_status = None
        self.code_review = None
        self.verified = None
        self.localrepo = localrepo

    def load_data(self, infos):
        for k, v in infos.iteritems():
            if k == 'id':
                k = 'uuid'
            elif k == 'status':
                k = 'remote_status'
            setattr(self, k, v)

    def submit(self):
        return self.remote.submit_change(self.number, self.patchset_number)

    def approve(self):
        return self.remote.approve_change(self.number, self.patchset_number)

    def reject(self):
        return self.remote.reject_change(self.number, self.patchset_number)

    def upload(self, reviewers=None):
        result_data = self.remote.upload_change(self.branch, self.branch, self.topic, reviewers=reviewers)
        if result_data:
            self.load_data(result_data)
            #self.number = result_data['number']
            #self.uuid = result_data['uuid']
            #self.status = result_data['status']
            #self.patchset_number = result_data['patchset_number']
            log.info("Recombination with Change-Id %s uploaded in replica gerrit with number %s" % (self.uuid, self.number))
        else:
            return False
        return True

    def is_approved(self):
        try:
            if self.code_review >= 2 and self.verified >= 1:
                return True
        except AttributeError:
            return False
        return False

    def abandon(self):
        if self.remote_status == "DRAFT":
            self.remote.publish_change(self.number, self.patchset_number)
        self.remote.abandon_change(self.number, self.patchset_number)

    def comment(self, comment_message, verified=None, code_review=None):
        self.remote.comment_change(self.number, self.patchset_number, comment_message, verified=verified, code_review=code_review)

    def comment_data(self, data, verified=None, code_review=None):
        comment_message = yaml.loads(data)
        self.remote.comment_change(self.number, self.patchset_number, comment_message, verified=verified, code_review=code_review)

    def load_from_remote(self, search_value, branch=None):
        data = self.remote.get_change_data(search_value, branch=branch)
        log.debugvar('data')
        self.load_data(data)

    def prepare_backport(self, remote, target_branch):
        if len(self.parents) > 1:
            backport_revision = self.parents[1]
        else:
            backport_revision = self.revision
        self.backport = Backport(backport_revision, target_branch, remote=remote, localrepo=self.localrepo)


class Backport(Change):


    def __init__(self, revision, target_branch, remote=None, infos=None, localrepo=None):
        super(Backport, self).__init__(remote=remote, infos=infos, localrepo=localrepo)
        remote = self.remote
        self.pick_revision = revision
        self.branch = target_branch


    def auto_attempt(self, target_branch):
        self.localrepo.cherrypick(self.branch, self.pick_revision)

    def analyze_comments(self):
        # comments may update metadata too
        # comments with actions will be acknoledged with
        # action:
        #   comment-id: comment id
        #   outcome: completed
        comments_metadata = dict()
        comment_commands = ["DISCARD"]
        # Maybe it's better to start yaml comments with ---
        if self.comments:
            for comment in self.comments:
                log.debugvar('comment')
                try:
                    comment_metadata = yaml.load(comment['message'])
                    if 'user-request' in comment_metadata and str(comment_metadata['user-request']['comment-id']) in self.user_requests:
                        self.user_requests[str(comment_metadata['user-request']['comment-id'])]['outcome'] = comment_metadata['user-request']['outcome']
                        comment_metadata.pop('user-request')
                    comments_metadata.update(comment_metadata)
                except (ValueError, yaml.scanner.ScannerError,yaml.parser.ParserError):
                    for line in comment['message'].split('\n'):
                        for cc in comment_commands:
                            rs = re.search('^%s$' % cc, line)
                            if rs is not None:
                                self.user_requests[str(comment['timestamp'])] = dict()
                                self.user_requests[str(comment['timestamp'])]['type'] = cc
                                self.user_requests[str(comment['timestamp'])]['outcome'] = "open"

        return comments_metadata

    def serve_requests(self):
        ur = self.user_requests
        log.debugvar('ur')
        if self.user_requests:
            log.info("Serving user requests in recombination %s comments" % self.uuid)
            for comment_id in self.user_requests:
                if self.user_requests[comment_id]['outcome'] != "completed":
                    if self.user_requests[comment_id]['type'] == "DISCARD":
                        served = { 'user-request': { 'comment-id' : comment_id, 'type': self.user_requests[comment_id]['type'], 'outcome': 'completed'}, 'recombine-status': 'DISCARDED'}
                        comment = yaml.safe_dump(served)
                        self.comment(comment, code_review="-2")
                        raise ValueError
                        self.abandon()

    def request_human_resolution(self, failure):
        self.branch = self.failure_branch
        self.upload()
        comment = failure.args[0]
        diffs = failure.args[1]
        for diff_file, diff_output in diffs.iteritems():
            comment += "\nconflics in file %s\n" % diff_file
            comment += diff_output
        self.comment(comment)

    def mangle_commit_message(self, commit_message):
        try:
            upstream_branch_name = self.evolution_change.branch.split('/')[1]
        except IndexError:
            upstream_branch_name = self.evolution_change.branch
        upstream_string = "\nUpstream-%s: %s\n" % (upstream_branch_name, self.evolution_change.url)
        commit_message = re.sub('(Change-Id: %s)' % self.evolution_change.uuid, '%s\g<1>' % (upstream_string), commit_message)
        commit_message = commit_message + "\n(cherry picked from commit %s)" % (self.evolution_change.revision)
        return commit_message

    def missing(self):
        try:
            self.attempt()
            log.debug("Recombination with patches successful, ready to create review")
        except RecombinationFailed as e:
            self.upload()
            status = e.args[0]
            suggested_solution = e.args[1]
            if not suggested_solution:
                suggested_solution=" No clue why this may have happened."
            message = '''Cherry pick failed with status:
    %s

%s

Manual conflict resolution is needed. Follow this steps to unblock this recombination:
    git review -d %s
    git cherry-pick -x %s

solve the conflicts, then

    git commit -a --amend

changing recombine-status to SUCCESSFUL.
Limit any eventual other modifications to sources.main.body *ONLY*, then update review as usual with

git review -D

If you decide to discard this pick instead, please comment to this change with a single line: DISCARD''' % (status, suggested_solution, self.number, self.evolution_change.revision )
            self.comment(message, verified="-1")
        else:
            self.upload()

    def approved(self):
        try:
            self.underlayer.format_patch(self)
            self.backport_change.topic ='automated_proposal'
            self.backport_change.upload(reviewers=self.backport_change.reviewers, successremove=False)
        except UploadError:
            log.error("Mannaggai")
        message = self.backport_change.post_create_comment['message']
        self.backport_change.comment(message, code_review=self.backport_change.post_create_comment['Code-Review'], verified=self.backport_change.post_create_comment['Verified'])
        self.comment("backport-id: %s" % self.backport_change.uuid)


