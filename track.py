
upstream = git
upstream = gerrit
    git init
    git add remote
    git fetch --tags
replica = gerrit

last tag = XXXXX



def check upstream
    upstream.get_revision_from_tag(last_tag)
    check_interval(upstream, replica)




def pull chain
    replica.get_top_of_chain
        for change in chain:
            if change.neededby == None
                top_of_chain = change
                break
    replica.checkout current-patches top of chain
        git fetch top_of_chain['current-patch-set']['url']
        git checkout FETCH_HEAD
        git checkout -b patches_top

def test merge
    cherrypick_test

    if ok
        push review
        gerrit push

    if fail
        autoresolution
        compare patches
        mangle chain
        push new chain


revisions_list = check_upstream(last_tag)
