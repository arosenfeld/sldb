from Bio.Seq import Seq
import sldb.identification.germlines as germ;

j_anchors = {
    'IGHJ1|4|5': 'TGGTCACCGTCTCCTCAG',
    'IGHJ2': 'TGGTCACTGTCTCCTCAG',
    'IGHJ3': 'TGGTCACCGTCTCTTCAG',
    'IGHJ6': 'CGGTCACCGTCTCCTCAG',
}

v_regex = [
    'GAC.{15}TGT',
    'GAC.{15}TG',
]

dc_final_aas = ['YY', 'YC', 'YH']

def all_j_anchors():
    max_size = max(map(len, j_anchors.values()))
    for trim in range(0, max_size, 3):
        for j, f in j_anchors.iteritems():
            new_f = f[0:len(f) - trim]
            if len(new_f) >= 12:
                yield new_f, f, j
