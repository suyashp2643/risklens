import csv
import hashlib
import json
import pytest
from pydantic import ValidationError
from risklens.demo import generate, write_csv
from risklens.ingestion import load_dataset, DatasetError
from risklens.schemas import Price, Portfolio

@pytest.fixture
def demo(tmp_path):
    root=tmp_path/'demo'
    generate(root)
    return root

def test_clean_dimensions_and_labels(demo):
    d=load_dataset(demo/'clean')
    assert len(d['prices'])==3024
    assert len(d['calendar'])==756
    assert d['provenance'].mode=='synthetic'
    assert sum(w.weight for w in d['portfolio'].weights)==pytest.approx(1)

def test_byte_reproducibility_and_manifest(demo,tmp_path):
    other=tmp_path/'other'
    generate(other)
    assert (other/'manifest.json').read_bytes()==(demo/'manifest.json').read_bytes()
    for name,digest in json.loads((demo/'manifest.json').read_text())['files'].items():
        assert hashlib.sha256((demo/name).read_bytes()).hexdigest()==digest

def test_no_overwrite(demo):
    before=(demo/'clean/prices.csv').read_bytes()
    with pytest.raises(FileExistsError): generate(demo)
    assert (demo/'clean/prices.csv').read_bytes()==before

@pytest.mark.parametrize('fixture,message',[('missing','missing expected'),('duplicate','duplicate date')])
def test_integrity_fixtures_block(demo,fixture,message):
    with pytest.raises(DatasetError,match=message): load_dataset(demo/fixture)

@pytest.mark.parametrize('fixture',['split','stale'])
def test_suspicious_valid_rows_preserved(demo,fixture):
    d=load_dataset(demo/fixture)
    assert len(d['prices'])==3024
    assert (demo/fixture/'prices.csv').read_bytes()!=(demo/'clean/prices.csv').read_bytes()

@pytest.mark.parametrize('value',[0,-1,'nan','inf','-inf'])
def test_invalid_prices(value):
    with pytest.raises(ValidationError): Price(date='2024-01-02',instrument='A',adjusted_close=value)

@pytest.mark.parametrize('weights',[[{'instrument':'A','weight':0.5}],[{'instrument':'A','weight':1.1}],[{'instrument':'A','weight':0.5},{'instrument':'A','weight':0.5}]])
def test_invalid_weights(weights):
    with pytest.raises(ValidationError): Portfolio(base_currency='USD',valuation=100,weights_as_of='2024-01-02',convention='constant_weight_daily_rebalanced',weights=weights)

def test_unknown_instrument(demo):
    path=demo/'clean/prices.csv'
    with path.open() as f: rows=list(csv.DictReader(f))
    rows[0]['instrument']='UNKNOWN'
    write_csv(path,['date','instrument','adjusted_close'],rows)
    with pytest.raises(DatasetError,match='unknown price'): load_dataset(demo/'clean')

def test_calendar_date_outside_bounds(demo):
    path=demo/'clean/prices.csv'
    with path.open() as f: rows=list(csv.DictReader(f))
    rows[0]['date']='2020-01-01'
    write_csv(path,['date','instrument','adjusted_close'],rows)
    with pytest.raises(DatasetError,match='outside supplied calendar'): load_dataset(demo/'clean')
