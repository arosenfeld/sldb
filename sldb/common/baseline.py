import collections
import csv
import os
import json
import shlex
import subprocess

import sldb.common.config as config
from sldb.common.models import Clone, CloneStats, Sequence, SequenceCollapse
import sldb.common.modification_log as mod_log
import sldb.util.concurrent as concurrent

TEST_FOCUSED = 1
TEST_LOCAL = 2
SPECIES_HUMAN = 1
SPECIES_MOUSE = 2
SUB_UNIFORM = 0
SUB_SMITH = 1
MUT_UNIFORM = 0
MUT_SHAPIRO = 1
CONSTANT_BOUNDARIES = [1, 26, 38, 55, 65, 104]

SEQ_CLONAL = 1
FIX_INDELS = 1


def get_selection(session, clone_id, script_path, samples=None,
                  remove_single_mutations=False,
                  temp_dir='/tmp',
                  test_type=TEST_FOCUSED,
                  species=SPECIES_HUMAN,
                  sub_model=SUB_UNIFORM,
                  mut_model=MUT_UNIFORM):
    clone = session.query(Clone).filter(Clone.id == clone_id).first()
    last_region = CONSTANT_BOUNDARIES[-1] + clone.cdr3_num_nts // 3
    boundaries = '{}:{}'.format(':'.join(map(str, CONSTANT_BOUNDARIES)),
                                last_region)
    if samples is not None:
        unique_id = '_{}_{}'.format(clone_id, '_'.join(map(str, samples)))
    else:
        unique_id = '_{}_{}'.format(clone_id, '_ALL')
    input_path = os.path.join(temp_dir, 'clone{}.fasta'.format(unique_id))
    out_path = os.path.join(temp_dir, 'output{}'.format(unique_id))
    read_path = os.path.join(temp_dir, 'output{}{}.txt'.format(unique_id,
                             clone.id))

    _make_input_file(session, input_path, clone, samples,
                     remove_single_mutations)
    cmd = 'Rscript {} {} {} {} {} {} {} {} {} {} {}'.format(
        script_path, test_type, species,
        sub_model, mut_model, SEQ_CLONAL,
        FIX_INDELS, boundaries, input_path, out_path,
        clone.id)
    proc = subprocess.Popen(shlex.split(cmd),
                            cwd=os.path.dirname(script_path),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    proc.communicate()

    with open(read_path) as fh:
        output = _parse_output(session, clone, fh)

    os.unlink(input_path)
    os.unlink(read_path)
    os.unlink(os.path.join(
        temp_dir, 'output{}{}.RData'.format(unique_id, clone.id)))

    return output


def _make_input_file(session, input_path, clone, samples,
                     remove_single_mutations):
    with open(input_path, 'w+') as fh:
        fh.write('>>>CLONE\n')
        fh.write('>>germline\n')
        fh.write('{}\n'.format(clone.consensus_germline))

        seqs = session.query(
            Sequence.sequence,
            Sequence.mutations_from_clone
        ).join(SequenceCollapse).filter(
            Sequence.clone == clone
        )
        if samples is None:
            seqs = seqs.filter(SequenceCollapse.copy_number_in_subject > 0)
        else:
            seqs = seqs.filter(Sequence.sample_id.in_(samples),
                               Sequence.copy_number > 0)

        if remove_single_mutations:
            removes = collections.Counter()
            seqs = seqs.all()
            # Iterate over each sequence and increment the count for each
            # mutation in the counter
            for seq in seqs:
                removes.update({
                    (i, seq.sequence[i]): 1 for i in
                    map(int, json.loads(seq.mutations_from_clone).keys())
                })

            # Filter out the mutations which occur more than once
            removes = [mut for mut, cnt in removes.iteritems() if cnt == 1]

            # Remove the remaining mutations, each of which only occurs once
            updated_seqs = []
            for seq in seqs:
                ns = list(seq.sequence)
                for pos, to_nt in removes:
                    if ns[pos] == to_nt:
                        ns[pos] = clone.consensus_germline[pos]
                updated_seqs.append(''.join(ns))
        else:
            updated_seqs = map(lambda s: s.sequence, seqs)

        for i, seq in enumerate(updated_seqs):
            fh.write('>{}\n{}\n'.format(i, seq))


def _parse_output(session, clone, fh):
    reader = csv.DictReader(fh, delimiter='\t')
    for row in reader:
        if row['Type'] == 'Sequence':
            del row['Type']
            del row['ID']
            row = {k: v.strip() for k, v in row.iteritems()}
            row = {
                k: v.strip() if v == 'NA' else float(v.strip()) for k, v in
                row.iteritems()
            }
            return row


class SelectionPressureWorker(concurrent.Worker):
    """A worker class for calculating selection pressure.  This worker will
    accept one clone at a time for parallelization.

    :param Session session: The database session

    """
    def __init__(self, session, baseline_path, baseline_temp, regen):
        self._session = session
        self._baseline_path = baseline_path
        self._baseline_temp = baseline_temp
        self._regen = regen

    def do_task(self, clone_id):
        """Starts the task of calculation of clonal selection pressure.

        :param int args: The clone_id

        """

        if not self._regen:
            if self._session.query(CloneStats).filter(
                    CloneStats.clone_id == clone_id,
                    ~CloneStats.selection_pressure.is_(None)
                    ).first() is not None:
                return

        self._print('Clone {}'.format(clone_id))
        sample_ids = map(lambda c: c.sample_id, self._session.query(
                CloneStats.sample_id
            ).filter(
                CloneStats.clone_id == clone_id
            )
        )

        for sample_id in sample_ids:
            self._process_sample(clone_id, sample_id,
                                 single=len(sample_ids) == 1)
        self._session.commit()

    def _process_sample(self, clone_id, sample_id, single):
        """Processes selection pressure for one sample (or the aggregate of all
        samples).  If ``sample_id`` is None the pressure for all sequences in
        the clone is calculated.  If ``single`` is specified, the clone only
        occurs in one sample and the entry with ``sample_id=None`` should be
        the same as for the one sample.

        :param int clone_id: The ID of the clone
        :param int sample_id: The ID of a sample in which the clone exists
        :param bool single: If the clone only occurs in one sample

        """

        selection_pressure = {
            'all': get_selection(
                self._session, clone_id, self._baseline_path,
                samples=[sample_id] if sample_id is not None else None,
                remove_single_mutations=False,
                temp_dir=self._baseline_temp),
            'multiples': get_selection(
                self._session, clone_id, self._baseline_path,
                samples=[sample_id] if sample_id is not None else None,
                remove_single_mutations=True,
                temp_dir=self._baseline_temp)
        }
        self._session.query(CloneStats).filter(
            CloneStats.clone_id == clone_id,
            CloneStats.sample_id == sample_id
        ).first().selection_pressure = json.dumps(selection_pressure)

        # If this clone only appears in one sample, the 'total clone' pressure
        # is the same as for the single sample
        if single:
            self._session.query(CloneStats).filter(
                CloneStats.clone_id == clone_id,
                CloneStats.sample_id.is_(None)
            ).first().selection_pressure = json.dumps(selection_pressure)

    def cleanup(self):
        self._session.commit()
        self._session.close()


def run_selection_pressure(session, args):
    mod_log.make_mod('clone_pressure', session=session, commit=True,
                     info=vars(args))

    if args.clone_ids is not None:
        clones = args.clone_ids
    elif args.subject_ids is not None:
        clones = map(lambda c: c.id, session.query(Clone.id).filter(
            Clone.subject_id.in_(args.subject_ids)).all())
    else:
        clones = map(lambda c: c.id, session.query(Clone.id).all())
    clones.sort()

    tasks = concurrent.TaskQueue()
    print ('Creating task queue to calculate selection pressure for {} '
           'clones.').format(len(clones))
    for cid in clones:
        tasks.add_task(cid)

    for i in range(0, args.nproc):
        session = config.init_db(args.db_config)
        tasks.add_worker(SelectionPressureWorker(session, args.baseline_path,
                                                 args.temp, args.regen))

    tasks.start()
