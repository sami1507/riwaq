"""Repeat the affected prior file/task path without overwriting Batch03 evidence."""
import os
from pathlib import Path
import pytest

@pytest.mark.skipif(os.environ.get('RIWAQ_TEST_BATCH04_PRIOR')!='1',reason='Explicit existing pinned local Docker regression; no downloads')
def test_existing_data_artifact_pipeline_after_productivity_integration(tmp_path,monkeypatch):
    import test_skill_batch03_data_api as prior
    target=Path(__file__).resolve().parents[1]/'reports/skills-batch-04-20261116/prior-data'
    target.mkdir(parents=True,exist_ok=True)
    monkeypatch.setattr(prior,'OUT',target)
    prior.test_real_csv_clean_stats_join_aggregate_chart_and_scope(tmp_path,monkeypatch)
