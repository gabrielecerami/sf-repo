
class PushError(Exception):
    pass

class MergeError(Exception):
    pass

class RemoteFetchError(Exception):
    pass

class TestError(Exception):
    pass
class RecombinationApproveError(object):
    pass
class RecombinationSubmitError(object):
    pass
class RecombinationSyncReplicaError(object):
    pass

class DecodeError(Exception):
    pass

class UploadError(Exception):
    pass

class SubmitError(Exception):
    pass

class PushMergeError(Exception):
    pass

class AttemptError(Exception):
    pass

class RecombinationFailed(Exception):
    pass

class RecombinationDataExpired(Exception):
    pass

class RecombinationCanceledError(Exception):
    pass

class ConstrainViolationError(Exception):
    pass
