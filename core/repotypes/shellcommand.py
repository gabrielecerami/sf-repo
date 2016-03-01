import subprocess
from ..colorlog import log

def shell(commandline, stdin=None, show_stdout=True, show_stderr=True, remove_blank=True, output_mode="list"):
    # TODO: implement output_mode = LIST, TEXT, SINGLE_LINE, SINGLE_VALUE
    process = subprocess.Popen(commandline, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    if stdin is not None:
        process.stdin.write(stdin)
    process.output, process.errors = process.communicate()
    process.output = process.output.split('\n')
    process.errors = process.errors.split('\n')
    if process.returncode == 0:
        outlog = log.success
    else:
        outlog = log.error
    log.info("---- executing command: %s" % commandline)
    log.info("---- stdout:")
    if show_stdout:
        for line in process.output:
            outlog(line)
    else:
        outlog("*** Suppressed")
    log.info("---- stderr:")
    if show_stderr:
        for line in process.errors:
            outlog(line)
    else:
        outlog("*** Suppressed")
    log.info("---- end command")
    if remove_blank:
        # remove blank lines from output for further processing
        while '' in process.output:
            process.output.remove('')
        while '' in process.errors:
            process.errors.remove('')
    return process


