# MechGym — roadmap

This is the plan for the project as a whole: where it is going, in what order, and what "done" means
at each stage. `design.md` describes the principles; `datasheet.md` describes what exists today.
This file changes as the project does. When a stage finishes or a decision changes the plan, update
the status table and add a line to the changelog at the end.

## Aim

Training and evaluation environments in which an AI agent does the work of a computational-mechanics
engineer or researcher, with a reward that can be trusted. The reward comes from a verifier that
re-runs the agent's model or design itself and compares it with a reference whose own error is
known. "Trusted" is measured: verifiers are validated against analytical solutions and published
benchmarks, and red-teamed by models trying to game them.

The long-term goal is tasks that take an agent hours to days: whole projects and open research
questions. They should still be graded automatically and without a rubric judged by another model.

## Task horizon ladder

The tasks are organized by how long and open-ended they are. Each level needs a different way of
verifying the result.

| Level | What the agent does | Typical length | How the result is verified | Status |
|---|---|---|---|---|
| H0 | One analysis with a closed-form answer (Lamé, Kirsch, Prandtl) | minutes | re-solve the agent's model; compare with the closed form | done (T0, T0-A, T0-F); saturated for current frontier models |
| H1 | One analysis without a closed form | under 1 h | reference from a converged numerical solution computed and checked by the verifier (mesh refinement, extrapolation, cross-check between two codes) | next |
| H2 | A multi-step engineering decision: check, design, back-analyze | about 1 h | re-analyze the submitted design or surface; hidden ground truth for back-analysis | done (T1, T2, T3) |
| H3 | A project of many stages, with information released over time | hours | the final design is re-analyzed, and each stage is checked against a hidden ground model that generates the monitoring data the agent sees | planned |
| H4 | A research task: find, calibrate, reproduce or falsify | hours to days | hidden ground truth, verifier re-run, or checking against a set of known cases the agent does not see | planned |

### H1 — single analyses without closed forms

These remove the main weakness of H0, which is that the answer is in textbooks. Candidates:

- Strip footing on Mohr–Coulomb soil with self-weight (N_γ). The reference is exact numerical
  values from the method of characteristics.
- Footings on layered or strength-increasing clay, and eccentric or inclined loads.
- Tunnels with a lining, i.e. ground–support interaction.
- Consolidation under staged loading, and plastic cavity expansion (Salençon) as a check.

The verifier computes the reference with its own converged model. Its discretization error is
reported the same way the datasheet reports it for H0.

### H3 — long-horizon projects

The model for this level is how a real slope or excavation project runs:

1. Desk study and site data with noise and gaps.
2. A first design.
3. Construction in stages, with monitoring readings released after each stage.
4. Back-analysis and a revised design.
5. A final report of numbers.

The environment keeps a hidden ground model and generates the readings the agent receives. The
reward combines:

- the safety and cost of the final design, re-analyzed by the verifier;
- whether each stage's prediction matched what the hidden model then produced.

First instance: **T4 staged excavation**, with finite-element strength reduction cross-checked
against limit equilibrium.

### H4 — research tasks

Research tasks still need an answer that can be checked. Four ways to get one, in order of how far
they reach:

1. **Checking is easier than solving.** The verifier can re-analyze any answer without knowing the
   best one. T1 already works this way: a surface more critical than the verifier's own search
   becomes the reference. Scores should be able to show an agent beating the reference instead of
   capping it at 1 (T2 currently caps it).
2. **The environment holds the truth.** A hidden model generates the data, so no human needs to
   have solved the problem (T3, H3).
3. **Physical consistency.** Equilibrium, conservation, convergence, the second law, and agreement
   between independent methods can reject wrong answers without a reference.
4. **Open-ended discovery.** A result that is new to everyone cannot be graded in advance. This
   project does not claim to measure it.

Candidate types:

- **Calibration.** Fit a constitutive model to laboratory test curves generated from hidden
  parameters, then predict a test the agent has not seen.
- **Discovery against hidden truth.** Find the governing mechanism or parameter from a set of
  experiments the agent designs and runs within a compute budget.
- **Reproduction.** Reproduce a published numerical result from the paper's description. Graded
  against the published value and the verifier's own run.
- **Falsification.** Find a case where two accepted methods disagree by more than a stated
  amount. The verifier re-runs both methods.
- **Verifier construction.** Write a verifier for a new problem. It is scored by whether it
  accepts correct submissions and rejects a hidden set of seeded wrong ones.

**First H4 task (flagship): soil constitutive calibration and prediction.** The agent receives a
laboratory programme generated from a hidden constitutive model (for example Modified Cam-Clay or a
hardening-soil model) with realistic noise:

- drained and undrained triaxial tests;
- tests at several consolidation pressures and overconsolidation ratios;
- oedometer tests.

The agent identifies and calibrates a model and then predicts a boundary-value problem it has not
seen, such as embankment settlement and excess pore pressure during and after construction. The
grade is the prediction against the hidden model's own finite-element solution.

The task contains deliberate confounds, for example:

- rate effects that look like drainage effects;
- sample disturbance that looks like a lower preconsolidation pressure.

It is built at the scale of Terminal-Bench-Science tasks (several hours, expert-authored) and
procedurally, so it serves both as an evaluation and as a family of training instances.
Geomechanics is not covered by Terminal-Bench-Science v0.1, and its solid-mechanics calibration
task (soft polymers) is the closest relative. This task should complement it, not duplicate it.

## Standards every release meets

- **Verifiers validated before tasks depend on them.** Errors are reported against closed forms
  and published benchmarks.
- **Red-teamed by models before release.** Every exploit found becomes a negative control in the
  tests.
- **Oracle and do-nothing controls.** Oracle solutions score 1.0 and a do-nothing agent scores 0
  on every task.
- **Baselines.** At least 5 trials per task, reported per task type with 95% intervals. Every
  failure is classified: model error, task-statement problem, or infrastructure. The
  classification is published.
- **Held-out suite.** Same composition as the public suite, drawn from private seeds, used to
  detect contamination.
- **Pinned and reproducible.** Images pinned by digest, generators seeded, suites versioned.
- **Method battery.** Each task type ships two sets of method variants, and the datasheet reports
  the measured margin between them:
  - plausible but wrong variants, each of which must fail. Examples are plane stress, too small a
    domain, a locking element, an omitted pore pressure, and an uncorrected confound.
  - legitimate alternative methods and ways of writing the same model, each of which must pass.
    Examples are other element types, other meshes, quarter or full models, and equivalent input
    syntax.

  The baseline on Eval v0.1 found statement problems that a battery of the second kind would have
  caught before release.
- **Designed knowledge gates.** Where possible a task contains at least one effect that an
  unaware method gets wrong without any visible sign in the data or the fit, such as volumetric
  locking under near-incompressibility. A gate is documented, together with the evidence that the
  wrong method fails it.
- **Expert authorship.** Each task type records its author, the author's relevant experience, and
  an estimate of expert time.

## Stages

| Stage | Content | Exit criteria | Status |
|---|---|---|---|
| 1. Foundations | LE verifier and its validation; T0–T3; Harbor packaging; three solvers in containers; two red-team rounds; first baseline | Eval v0.1 with oracle 1.0, do-nothing 0, and a published baseline | done (2026-11-15) |
| 2. Eval v0.2 | H1 tasks replace the saturated H0 ones; failures from v0.1 fixed in the task statements; a method battery for every task type; T2 scores that can exceed the reference; second model family; human expert baseline on a subset; images pinned by digest; license | No task type above 0.9 mean for the strongest model tested; every failure classified; every battery variant on the expected side | in progress: batteries for all types, T1 knowledge gates and statement fixes, uncapped T2 diagnostics done |
| 3. Long horizon | T4 staged excavation; first H3 multi-stage project with released monitoring data | Oracle and do-nothing controls; baseline with trajectory review | planned |
| 4. Research tasks | First H4 tasks: concrete fracture size effect on real data (done), the soil calibration flagship, a fracture task with crack paths not known in advance, then verifier construction | As above, plus a check that no task can be solved without running analyses | in progress: `tasks/concrete-fracture-size-effect` done (oracle 1, method battery, two Claude Opus 5.5 trials both solved it) |
| 5. Training signal | Small RL run on generated instances; evaluation on the held-out suite | Improvement on held-out tasks that is not explained by reward hacking (checked by trajectory review) | planned |

## Open questions

- **References for H1.** How to bound the reference error when there is no closed form. The
  options are refining until the change is below a tolerance, cross-checking two codes, or both.
- **Scoring long tasks.** Credit for intermediate stages versus only the final result in H3.
  Intermediate checks give a denser signal but can teach the agent to optimize the checkpoint.
- **Compute budgets.** Whether to give agents a fixed solver-time budget. Real engineering has
  one, and it makes efficiency part of the task.
- **Binary or continuous reward.** Terminal-Bench-Science grades each task pass or fail and keeps
  continuous scores for analysis only. MechGym uses continuous rewards because they give a denser
  training signal. A release could report both, with a stated pass threshold per task type.
- **Contributing to Terminal-Bench-Science.** Whether to contribute the geomechanics flagship
  there, alongside keeping its procedural family here.

## Changelog

- 2026-11-15: first version, after the Sonnet 5 baseline on Eval v0.1.
- 2026-11-15: after reviewing Terminal-Bench-Science v0.1, added method batteries, designed
  knowledge gates and expert authorship to the release standards; the four ways of grading research
  tasks; the soil-calibration flagship as the first H4 task; uncapped T2 scores; open questions on
  binary rewards and contributing.
- 2026-11-16: Stage 2 status updated (method batteries for all task types, T1 knowledge gates, uncapped T2 diagnostics). The T2 battery shows the single-plane reference is beaten by 12-70% with a two-segment face, so T2 needs a stronger reference.
- 2026-11-20: first H4 task, `tasks/concrete-fracture-size-effect`: real three-point-bend data on one concrete batch (D = 40–215 mm given, D = 500 mm withheld), outcome-only grading with the verifier re-running the agent's model, method battery, oracle 1. Two Claude Opus 5.5 trials solved it in 29 and 43 minutes with a self-written cohesive-crack model, so the next fracture task needs crack paths that are not known in advance.
