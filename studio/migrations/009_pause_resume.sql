-- Pause + resume support for long-running pipelines.
--
-- studio_strategies.partial_state_json holds checkpointed intermediate
-- results (topicgen_results / scheduler_result / drafter pool results)
-- so when the user cancels mid-expand and clicks 'resume' later, the
-- pipeline skips already-completed stages instead of redoing them.

ALTER TABLE studio_strategies ADD COLUMN partial_state_json TEXT;
ALTER TABLE studio_strategies ADD COLUMN paused_at_stage TEXT;
