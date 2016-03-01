import yaml
import re
from colorlog import log
from utils import *
from exceptions import *

yaml.add_representer(folded_unicode, folded_unicode_representer)
yaml.add_representer(literal_unicode, literal_unicode_representer)

class Change(object):

    def __init__(self, remote=None, infos=None):
        if infos:
            self.load_infos(infos)
        else:
            self.branch = None
            self.topic = None

        self.remote = None
        if remote:
            self.remote = remote
        self.remote_status = None
        self.code_review = None
        self.verified = None

    def load_data(self, infos):
        # self.__dict__.update(infos)
        self.revision = infos['revision']
        self.branch = infos['branch']
        if 'id' in infos:
            self.uuid = infos['id']
        elif 'uuid' in infos:
            self.uuid = infos['uuid']
        self.parent = infos['parent']
        self.previous_commit = infos['parent']
        if 'number' in infos:
            self.number = infos['number']
        if 'status' in infos:
            self.remote_status = infos['status']
        self.project_name = infos['project-name']
        if 'topic' in infos:
            self.topic = infos['topic']
        if 'patchset_number' in infos:
            self.patchset_number = infos['patchset_number']
        if 'patchset_revision' in infos:
            self.patchset_revision = infos['patchset_revision']
        if 'url' in infos:
            self.url = infos['url']
        if 'commit-message' in infos:
            self.commit_message = infos['commit-message']
        if 'comments' in infos:
            self.comments = infos['comments']
        if 'approvals' in infos:
            self.code_review = infos['approvals']['code-review']
            self.verified = infos['approvals']['verified']

    def submit(self):
        return self.remote.submit_change(self.number, self.patchset_number)

    def approve(self):
        return self.remote.approve_change(self.number, self.patchset_number)

    def reject(self):
        return self.remote.reject_change(self.number, self.patchset_number)

    def upload(self, reviewers=None, successremove=True):
        result_data = self.remote.upload_change(self.branch, self.topic, reviewers=reviewers, successremove=successremove)
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


class Recombination(Change):

    def __init__(self, underlayer, remote):
        self.underlayer = underlayer
        self.removed_commits = None
        self.backportid = None
        self.user_requests = dict()
        self.remote = remote

    def initialize(self, remote):
        self.commit_message = None
        self.status = "UNATTEMPTED"
#        ch['comments'][-4]['message'].split('\n'):
        super(Recombination, self).__init__(remote=remote)

    def set_status(self, metadata=None):
        self.status = "MISSING"
        if metadata and 'recombine-status' in metadata:
            self.status = metadata['recombine-status']
        if self.remote_status == 'NEW' or self.remote_status == 'DRAFT' and self.status == "SUCCESSFUL":
            self.status = 'PRESENT'
        if self.remote_status != "MERGED" and self.is_approved():
            self.status = "APPROVED"
        if self.status == "DISCARDED" and self.remote_status == "ABANDONED":
            # This combination of statuses means that this recombination attempt
            # was canceled and will not be reattempted
            log.warning("Recombination %s was discarded by user request, not reattempting" % self.uuid)
        if self.status == "ABANDONED" and self.remote_status == "ABANDONED":
            # this combination of statuses means that this recombination attempt
            # is completely abanoned and will be reattempted
            log.warning("Recombination %s was completely trashed, skipping and reattempting" % self.uuid)
            raise RecombinationCanceledError
        try:
            if self.backport_change:
                pass
        except AttributeError:
            pass

    def get_commit_message_data(self):

        commit_message_data = {
            "sources": {
                "main": {
                    "name": str(self.main_source_name),
                    "branch": str(self.main_source.branch),
                    "revision": self.main_source.revision,
                    "id": str(self.main_source.uuid)
                },
                "patches" : {
                    "name": str(self.patches_source_name),
                    "branch": str(self.patches_source.branch),
                    "revision": str(self.patches_source.revision),
                    "id": str(self.patches_source.uuid)
                },
            },
            "recombine-status": self.status,
        }
        try:
            commit_message_data['target-replacement-branch'] = str(self.target_replacement_branch)
        except AttributeError:
            pass
        try:
            commit_message_data['sources']['patches']['commit-message'] = literal_unicode(self.backport_change.commit_message)
        except AttributeError:
            pass
        try:
            commit_message_data['removed-patches-commits'] = self.removed_patches_commits
        except AttributeError:
            pass
        # metadata['sources']['patches']['commit-message']
        # if 'commit-message' in self.metadata['sources']['patches']:
        return commit_message_data

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

    def handle_status(self):
        self.serve_requests()
        if self.status == "MISSING":
            self.missing()
        elif self.status == "APPROVED":
            self.approved()
        elif self.status == "MERGED":
            self.merged()
        elif self.status == "PRESENT":
            self.present()
        elif self.status == "BLOCKED":
            self.blocked()

    def load_change_data(self, change_data):
        """ Common load operations for all recombination types """
        self.load_data(change_data)
        log.debug(self.commit_message)
        try:
            metadata = yaml.load(self.commit_message)
        except (ValueError, yaml.scanner.ScannerError,yaml.parser.ParserError):
            log.error("commit message not in yaml")
            raise DecodeError
        header = metadata['Recombination']
        recomb_header = header.split('~')[0]
        metadata['recomb-type'] = re.sub(':[a-zA-Z0-9]{6}', '',recomb_header)
        if 'recombine-status' in metadata:
            self.status = metadata['recombine-status']
        metadata.update(self.analyze_comments())
        self.set_status(metadata=metadata)
        return metadata
        #recomb_sources = metadata['sources']


class EvolutionDiversityRecombination(Recombination):

    def initialize(self, remote, evolution_change=None, diversity_change=None, backport_change=None):
        super(EvolutionDiversityRecombination, self).initialize(remote)
        try:
            self.evolution_change = evolution_change
            self.diversity_change = diversity_change
            self.backport_change = backport_change
            self.branch = "recomb-evolution-%s-%s" % (self.evolution_change.branch, self.evolution_change.revision)
            self.topic = self.evolution_change.uuid
        except NameError:
            raise MissingInfoError
        self.main_source = self.evolution_change
        self.patches_source = self.diversity_change
        self.main_source_name = 'evolution'
        self.patches_source_name = 'diversity'
        self.set_status()
        if self.backport_change.remote_status is not None:
            log.warning("recombination already backported in patches branch")
            self.status = ""
        else:
            self.backport_change.commit_message = self.mangle_commit_message(self.evolution_change.commit_message)
        self.follow_backport_status()

    def load_change_data(self, change_data, original_remote=None, patches_remote=None, diversity_change=None):
        self.main_source_name = 'evolution'
        self.patches_source_name = 'diversity'
        metadata = super(EvolutionDiversityRecombination, self).load_change_data(change_data)
        self.evolution_change = Change(remote=original_remote)
        self.evolution_change.load_from_remote(metadata['sources']['main']['id'], branch=metadata['sources']['main']['branch'])
        self.main_source = self.evolution_change
        # Set real commit as revision
        self.evolution_change.revision = metadata['sources']['main']['revision']
        self.diversity_change = diversity_change
        self.patches_source = self.diversity_change
        self.backport_change = Change(remote=patches_remote)
        if 'backport-id' in metadata and metadata['backport-id'] is not None:
            self.backport_change.load_from_remote(metadata['backport-id'], branch=metadata['sources']['patches']['branch'])
        else:
            self.backport_change.commit_message = metadata['sources']['patches']['commit-message']
            self.backport_change.branch = metadata['sources']['patches']['branch']
        if 'backport-test-results' in metadata:
            self.backport_change.post_create_comment = dict()
            self.backport_change.post_create_comment['message'] = metadata['backport-test-results']['message']
            self.backport_change.post_create_comment['Code-Review'] = metadata['backport-test-results']['Code-Review']
            self.backport_change.post_create_comment['Verified'] = metadata['backport-test-results']['Verified']
            self.backport_change.reviewers = metadata['backport-test-results']['reviewers']
        self.follow_backport_status()

    def attempt(self):
        self.underlayer.cherrypick_recombine(self)

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

    def follow_backport_status(self):
        if self.backport_change.remote_status == "MERGED":
            try:
                self.submit()
            except RecombinationSubmitError:
                log.error("Recombination not submitted")
        elif self.backport_change.remote_status == "ABANDONED":
            try:
                self.abandon()
            except RecombinationAbandonedError:
                log.error("Recombination not abandoned")

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

    def present(self):
        pass

    def blocked(self):
        pass


