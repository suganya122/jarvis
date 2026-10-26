"""OS-touching workers (hands, capture, voice), one process per capability family.

Workers import only from `contracts`; core never imports from here. Arrives in milestones 4, 5, 7.
"""
