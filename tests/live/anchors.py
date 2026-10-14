"""The pinned live sample.

Published studies change rarely. They are asserted loosely (invariants, not
upstream counts) so a curation event reads as an explicit "anchor changed"
failure rather than a mysterious assertion error. Measured shapes:

MetaboLights
    MTBLS1     132 samples / 220 metabolites, abundances for all 132
    MTBLS10     30 samples / 1496 metabolites, mass_to_charge + retention_time
    MTBLS744     4 inspected samples -> 2 natural keys (variant sheets), 62 metabolites
    MTBLS14005  study() works, inspect() reports "no ISA metadata files"

Workbench
    ST000001    24 samples / 1 analysis / 102 metabolites
    ST000002    12 samples / 1 analysis / 142 metabolites
    ST000004     1 sample  / 8 analyses / 540 metabolites
    ST004741 18476 samples, /metabolites returns [] ("not listed")
    ST999999  does not exist
"""

from __future__ import annotations

ML_ANCHORS = ("MTBLS1", "MTBLS10", "MTBLS744")
ML_ERROR = "MTBLS14005"

WB_ANCHORS = ("ST000001", "ST000002", "ST000004")
WB_EMPTY_METABOLITES = "ST004741"
WB_MISSING = "ST999999"
