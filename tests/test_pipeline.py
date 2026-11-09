import csv
import json
from pathlib import Path

import pytest

from rigpulse.detection import detect
from rigpulse.drift import drift_report, psi
from rigpulse.evaluation import evaluate
from rigpulse.ingest import ingest_csv
from rigpulse.simulate import generate
from rigpulse.storage import connect, summary


def test_reproducible_generator(tmp_path):
    a=generate(tmp_path/'a', rigs=2, hours=48, seed=13)
    b=generate(tmp_path/'b', rigs=2, hours=48, seed=13)
    assert Path(a['telemetry_path']).read_bytes()==Path(b['telemetry_path']).read_bytes()
    assert a['readings']==2*48*4
    assert a['injected_event_readings']>0


def test_idempotent_ingestion(populated):
    db, generated, loaded, _ = populated
    assert loaded['inserted']==3*100*4
    again=ingest_csv(generated['telemetry_path'], db)
    assert again['inserted']==0
    assert again['duplicates_skipped']==loaded['inserted']
    assert summary(db)['readings']==loaded['inserted']


def test_detection_idempotent(populated):
    db, _, _, first=populated
    second=detect(db)
    assert first==second
    assert summary(db)['alerts']==first['alerts_created']
    assert second['alerts_created']>0


def test_detected_incidents_and_metrics(populated):
    db, generated, _, _ = populated
    scores=evaluate(db, generated['truth_path'])
    assert scores['matched']==1200
    assert scores['tp']>0
    assert scores['recall']>=0.7
    assert 0 <= scores['precision'] <= 1
    assert 0 <= scores['f1'] <= 1


def test_no_orphan_alerts(populated):
    db, _, _, _=populated
    with connect(db) as conn:
        row=conn.execute('PRAGMA foreign_key_check').fetchall()
    assert row==[]


def test_csv_bad_row_aborts_without_partial_writes(tmp_path):
    path=tmp_path/'bad.csv'
    path.write_text('observed_at,rig_id,metric,value\n2026-01-01T00:00:00Z,RIG-A,temperature_c,34\n2026-01-01T01:00:00Z,RIG-A,pressure_bar,NaN\n')
    db=tmp_path/'db.sqlite'
    with pytest.raises(ValueError, match='line 3'):
        ingest_csv(path, db)
    assert not db.exists()


def test_psi_responds_to_large_shift():
    a=[float(i) for i in range(80)]
    assert psi(a,a)==pytest.approx(0,abs=1e-10)
    assert psi(a,[float(i+100) for i in range(80)])>0.25


def test_drift_report(populated):
    db,_,_,_=populated
    report=drift_report(db)
    assert len(report['metrics'])==4
    assert all('psi' in m for m in report['metrics'])


def test_cli_demo(tmp_path, capsys):
    from rigpulse.cli import main
    rc=main(['demo','--db',str(tmp_path/'demo.db'),'--out',str(tmp_path/'data'),
             '--rigs','2','--hours','64','--seed','9'])
    report=json.loads(capsys.readouterr().out)
    assert rc==0
    assert report['summary']['readings']==512
    assert report['evaluation']['matched']==512
