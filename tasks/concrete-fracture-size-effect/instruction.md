`/app/data/` holds laboratory fracture tests on concrete: notched and un-notched three-point bend
beams of depths 40, 92.8 and 215 mm, and companion tests on the same concrete
(`/app/data/README.md` describes the files). All beams are geometrically similar: span 2.176 D,
overall length 2.4 D, thickness 40 mm.

The same concrete was also used for beams of depth D = 500 mm (span 1088 mm, length 1200 mm,
thickness 40 mm) with relative notch depths alpha0 = 0.30, 0.15, 0.075 and 0.025. Build a finite element
model from the data you have and predict the load–opening response of these beams.

Deliver in `/app/results/`:

1. `model.py` with a function

   `simulate(D_mm, alpha0, h_mm, gauge_mm, opening_end_mm) -> dict`

   It runs your finite element model of a beam of depth `D_mm` (span, length and thickness as above),
   relative notch depth `alpha0` and element size `h_mm` in the region where the beam cracks. The
   loading continues until the opening over `gauge_mm` reaches `opening_end_mm`. The function returns
   `{"P_N": [...], "opening_mm": [...], "n_elements": int}`. The load and opening follow the same
   conventions as in the data.
2. `config.json` in the form `{"h_mm": {"40": h, "92.8": h, "215": h, "500": h}}`, giving the element
   size you use for each depth.
3. `predictions/D500_a0.30.csv`, `D500_a0.15.csv`, `D500_a0.075.csv`, `D500_a0.025.csv`, with columns
   `P_N` and `opening_mm`. Use `gauge_mm` = 25.4 for alpha0 = 0.30 and 162 for the others, and an
   opening of at least 0.5 mm. Produce them with `simulate` at your `config.json` element size.

Grading runs your `model.py` in a separate container that has the same preinstalled software and no
network access. It may import only preinstalled packages and files inside `/app/results/` (read-only during grading; each call
runs in its own writable working directory), and one
call must finish within 30 minutes on a single CPU core. The submission passes only if all of the
following hold:

- The predicted D = 500 mm curves agree with the measured mean response of the D = 500 mm beams, in
  both the peak load and the softening branch.
- `simulate` at your `config.json` element sizes reproduces your four prediction files.
- `simulate` reproduces the measured mean response of the beam groups you were given.
- The response does not depend on the mesh: `simulate(92.8, 0.30, h_mm, 12.8, 0.18)` at h_mm = 6, 3
  and 1.5 gives the same peak load and the same area under the load–opening curve, and the element count grows as
  h_mm decreases.

You have 8 hours (28800 seconds). The environment has no internet access; all software you may use is preinstalled.
