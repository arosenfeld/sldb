import json

from sqlalchemy import distinct, func

import sldb.common.config as config
from sldb.common.models import Clone, CloneStats, Sequence, SequenceCollapse
import sldb.common.modification_log as mod_log
from sldb.common.mutations import CloneMutations
import sldb.util.concurrent as concurrent


class CloneStatsWorker(concurrent.Worker):
    """A worker class for generating clone statistics.  This worker will accept
    one clone at a time for parallelization.

    :param Session session: The database session

    """
    def __init__(self, session):
        self._session = session

    def do_task(self, clone_id):
        """Starts the task of generating clone statistics for a given
        clone_id.
        :param int args: The clone_id for which to calculate statistics

        """

        existing = self._session.query(CloneStats).filter(
            CloneStats.clone_id == clone_id).first()

        if existing is not None:
            return

        self._print('Clone {}'.format(clone_id))
        sample_ids = map(lambda c: c.sample_id, self._session.query(
                distinct(Sequence.sample_id).label('sample_id')
            ).filter(
                Sequence.clone_id == clone_id
            )
        )
        if len(sample_ids) > 1:
            sample_ids.append(None)
        for sample_id in sample_ids:
            self._process_sample(clone_id, sample_id,
                                 single=len(sample_ids) == 1)

    def _process_sample(self, clone_id, sample_id, single):
        """Processes clone statistics for one sample (or the aggregate of all
        samples).  If ``sample_id`` is None the statistics for all sequences in
        the clone is generated.  If ``single`` is specified, the clone only
        occurs in one sample and the entry with ``sample_id=None`` should be
        the same as for the one sample.

        :param int clone_id: The ID of the clone
        :param int sample_id: The ID of a sample in which the clone exists
        :param bool single: If the clone only occurs in one sample

        """

        if sample_id is None:
            counts = self._session.query(
                func.count(Sequence.ai).label('unique'),
                func.sum(Sequence.copy_number).label('total')
            ).join(SequenceCollapse).filter(
                Sequence.clone_id == clone_id,
                SequenceCollapse.copy_number_in_subject > 0
            ).first()
        else:
            counts = self._session.query(
                func.count(Sequence.ai).label('unique'),
                func.sum(Sequence.copy_number).label('total')
            ).filter(
                Sequence.sample_id == sample_id,
                Sequence.clone_id == clone_id
            ).first()

        sample_mutations = CloneMutations(
            self._session,
            self._session.query(Clone).filter(Clone.id == clone_id).first()
        ).calculate(
            commit_seqs=sample_id is not None, limit_samples=[sample_id],
        )[sample_id]

        record_values = {
            'clone_id': clone_id,
            'unique_cnt': counts.unique,
            'total_cnt': counts.total,
            'mutations': json.dumps(sample_mutations.get_all()),
        }

        self._session.add(CloneStats(sample_id=sample_id, **record_values))

        # If this clone only appears in one sample, the 'total clone' stats are
        # the same as for the single sample
        if single:
            self._session.add(CloneStats(sample_id=None, **record_values))
        self._session.commit()

    def cleanup(self):
        self._session.commit()
        self._session.close()


def run_clone_stats(session, args):
    """Runs the clone statistics generation stage of the pipeline.
    :param Session session: The database session
    :param Namespace args: The arguments passed to the command

    """
    mod_log.make_mod('clone_stats', session=session, commit=True,
                     info=vars(args))

    if args.clone_ids is not None:
        clones = args.clone_ids
    elif args.subject_ids is not None:
        clones = map(lambda c: c.id, session.query(Clone.id).filter(
            Clone.subject_id.in_(args.subject_ids)).all())
    else:
        clones = map(lambda c: c.id, session.query(Clone.id).all())
    clones.sort()

    if args.regen:
        print 'Deleting old clone statistics for {} clones'.format(len(clones))
        session.query(CloneStats).filter(
            CloneStats.clone_id.in_(clones)
        ).delete(synchronize_session=False)
        session.commit()

    tasks = concurrent.TaskQueue()
    print 'Creating task queue to generate stats for {} clones.'.format(
        len(clones)
    )
    for cid in clones:
        tasks.add_task(cid)

    for i in range(0, args.nproc):
        session = config.init_db(args.db_config)
        tasks.add_worker(CloneStatsWorker(session))

    tasks.start()
