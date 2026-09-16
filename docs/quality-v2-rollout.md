# Quality v2 rollout decision

## Current decision

Do not replace the production 0–3 quality tier with metadata-only `quality-v2.0`.
The 100-episode calibration is complete, but paired testing shows material evidence
sensitivity rather than simple random disagreement.

As of the recorded calibration:

- five transcript-backed negative controls remained low quality;
- six metadata Q2/Q3 boundary cases have paired transcript assessments;
- transcript evidence raises boundary scores by more than three points on average;
- five of six boundary cases change tier;
- several substantive interviews were Q1 or Q2 from metadata and Q3 from transcript.

Therefore, metadata-only v2 scores are provisional. They may prioritize additional
evidence, but must not exclude episodes from production results.

## Revised primary-measure contract

`quality_score_10` may become primary only when one of these evidence states applies:

1. `transcript_used=1`, with confidence 2; or
2. a human-reviewed override with an audit reason.

Metadata-only assessments remain useful for:

- identifying obvious roundup/promotional negative controls;
- prioritizing transcription near the Q2/Q3 boundary;
- flagging low-confidence records for review;
- explaining why more evidence is required.

They must not silently demote or remove a legacy-retained episode.

## Atomic rollout

Two guarded targets are implemented:

- `quality-v2.0` (strict): every retained episode must have a confidence-2
  transcript assessment or reviewed override, and no boundary may remain unresolved.
- `quality-v2-hybrid` (staged): use a reviewed override first, then a confidence-2
  transcript score, and otherwise map the unchanged legacy tier to 0/3/6/9. This
  prevents metadata-only v2 from silently removing a legacy-retained episode.

Both targets use a 5/10 publication threshold. Activation is a versioned settings
transaction and is never implicit; the command is a dry run unless `--apply` is given.

When the evidence contract is satisfied for the retained corpus, use one database
transaction to:

1. verify the calibration report has no unresolved boundaries;
2. verify every affected retained episode has transcript-backed confidence 2 or a
   reviewed override;
3. set a versioned `primary_quality_measure` setting to `quality-v2.0`;
4. update exports and the result site to read score, confidence, rubric version, and
   explanation from the same assessment snapshot;
5. commit only after rebuilding and validating export/site staging outputs.

The legacy quality fields remain unchanged for rollback. Switching the versioned
setting back to `legacy` restores the previous behavior atomically.

## Consumer behavior

- CSV/XLSX: export `quality_score_10`, derived tier, confidence, evidence mode,
  rubric version, and reason together.
- Site: sort primarily by the selected version's score; show confidence and whether
  transcript evidence was used.
- Filters: use a 0–10 threshold only for transcript-backed or reviewed scores.
- Explanations: distinguish intrinsic-quality evidence from relevance and popularity.
- Unresolved metadata-only boundaries: retain the legacy production decision and place
  the episode in the transcription/review queue.

## Remaining work

- resolve the three long-form boundary cases or record reviewed overrides;
- accumulate transcript/review coverage until strict activation is practical;
- inspect staged site and CSV outputs before explicitly activating hybrid production.
