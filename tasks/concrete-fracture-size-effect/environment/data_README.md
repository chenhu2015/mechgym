# Data

All specimens were cast from one batch of normal-strength concrete (maximum aggregate size 10 mm)
and, unless stated otherwise, tested at an age of about 400 days.

## `beams/` and `specimens.csv`: three-point bend beams

One CSV per beam, columns:
- `P_N`: load applied by the testing machine, in N. The beam's own weight is not included.
- `opening_mm`: relative horizontal displacement, in mm, of two points on the bottom face, placed
  symmetrically about the mid-span section a distance `gauge_mm` apart (an extensometer across the
  notch mouth). The tests were run under control of this opening.

The records were low-pass filtered by the laboratory.

`specimens.csv` holds one row per beam, with all lengths measured on the specimen:
- `nominal_depth_mm`, `depth_mm`, `thickness_mm`;
- `span_mm` (distance between the supports) and `length_mm` (overall length; empty if not measured);
- `notch_depth_mm` (0 for un-notched beams);
- `mass_kg`;
- `gauge_mm`;
- `lab_note`.

The notches were sawn, about 1.8 mm wide with a roughly semicircular tip. The beams were loaded at mid-span through a steel block and
supported near both ends.

## `companion/`: other tests on the same concrete

Per specimen there is a nominal stress–strain record (`nominal_strain`, `nominal_stress_MPa`).
`definitions.json` gives how the laboratory defined both quantities for each test type. The
`specimens_*.csv` files are the laboratory's raw specimen sheets: dimensions in mm, masses, ages and
loading details.

| Folder | Test |
|---|---|
| `cylinder_compression_400d/`, `cylinder_compression_31d/` | compression of cylinders at 400 and 31 days |
| `cube_compression_40mm/`, `cube_compression_150mm/` | compression of cubes |
| `splitting_<size>/` | Brazilian splitting of specimens of several sizes, loaded through wooden strips |
| `direct_tension_notched/` | direct tension of notched prisms, tested at a later age (see `specimens_tension_notch.csv`) |
