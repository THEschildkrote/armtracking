"""Bounded online queries; failure returns no prior and preserves core scoring."""
import concurrent.futures as cf
import csv
import json
import math
import sys
import time
import uuid
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from .config import ROOT
from .logging import log
sys.path.insert(0,str(ROOT/'intent'))

class RoutineClient:
    def __init__(self,config):
        self.config=config;self.client=None
        self.pool=cf.ThreadPoolExecutor(max_workers=1,thread_name_prefix='routine-chalk')
        self.pending=None;self.down_until=0.;self.cache=None;self.cache_time=0.;self.cache_key=None
        self.last={};self.init_error=None;self.cloud_evidence={}
        if config.chalk_enabled:
            try:
                from chalk.client import ChalkClient
                from routine_features import RoutineRequest
                self.client=ChalkClient(default_request_timeout=10,default_connect_timeout=10)
            except Exception as e:self.init_error=type(e).__name__

    def _query(self,labels,as_of,hour,qid,timeout_s=None):
        timeout_s=self.config.chalk_timeout_s if timeout_s is None else timeout_s
        from routine_features import RoutineRequest as R
        recent=[];path=ROOT/'logs/grab_events.csv'
        if path.exists():
            with path.open(newline='') as f:recent=list(csv.DictReader(f))[-2000:]
        result=self.client.query(input={R.id:qid,R.user_id:self.config.user_id,R.as_of_iso:as_of,
            R.hour_bucket:hour,R.timezone:self.config.timezone,R.labels_json:json.dumps(labels),
            R.recent_events_json:json.dumps(recent),R.agent_weight:self.config.agent_weight,R.population_enabled:self.config.population_enabled},
            correlation_id=qid,include_meta=True,
            output=[R.prior_json,R.counts_7d_json,R.counts_14d_json,R.hour_frequency_json,R.agent_priors_json,R.population_prior_json,R.population_weight],
            request_timeout=timeout_s,connect_timeout=timeout_s)
        self.cloud_evidence={"population_prior":json.loads(result.get_feature_value(R.population_prior_json)),
            "population_weight":result.get_feature_value(R.population_weight),"deployment_id":getattr(result.meta,"deployment_id",None),
            "agent_priors":json.loads(result.get_feature_value(R.agent_priors_json))}
        priors=json.loads(result.get_feature_value(R.prior_json))
        if not isinstance(priors,dict) or any(k not in priors for k in labels) or any(
            isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<v<=1 for v in priors.values()):
            raise ValueError('Invalid Chalk probabilities')
        if abs(sum(priors.values())-1)>1e-5:raise ValueError('Unnormalized Chalk probabilities')
        return priors

    def warmup(self,labels,wait=False):
        """One bounded startup request; runtime stays on core scoring while it runs."""
        if not self.config.chalk_enabled or not self.client:
            if wait:raise RuntimeError('Chalk credentials unavailable')
            return
        if self.pending and not self.pending.done():
            future=self.pending
        else:
            def prepare():
                qid=uuid.uuid4().hex;started=time.perf_counter();now=datetime.now(timezone.utc)
                try:
                    value=self._query(sorted(set(labels)),now.isoformat(),
                        now.astimezone(ZoneInfo(self.config.timezone)).hour,qid,timeout_s=10)
                    log('chalk_queries.jsonl',query_id=qid,phase='startup',result='success',api_attempted=True,
                        latency_ms=round((time.perf_counter()-started)*1000,1),priors=value)
                    return value
                except Exception as e:
                    log('chalk_queries.jsonl',query_id=qid,phase='startup',result='fallback',api_attempted=True,
                        error=type(e).__name__,latency_ms=round((time.perf_counter()-started)*1000,1))
                    return None
            future=self.pool.submit(prepare);self.pending=future
        if wait and future.result(timeout=10) is None:
            raise RuntimeError('Chalk startup query failed; no demo fallback')

    def query(self,labels,*,as_of=None,hour=None,strict=False):
        labels=sorted(set(labels));now=datetime.now(timezone.utc)
        hour=now.astimezone(ZoneInfo(self.config.timezone)).hour if hour is None else hour
        as_of=as_of or now.isoformat();key=(tuple(labels),hour)
        if not self.config.chalk_enabled:
            if strict:raise RuntimeError('Enable chalk_enabled for live demo')
            return None
        if not strict and self.cache_key==key and time.monotonic()-self.cache_time<self.config.chalk_refresh_s:
            return self.cache
        if not strict and time.monotonic()<self.down_until:return None
        if self.pending and not self.pending.done():
            if strict:raise RuntimeError('Previous Chalk query still in progress')
            return None
        started=time.perf_counter();qid=uuid.uuid4().hex;error=None;priors=None
        attempted=self.client is not None
        try:
            if not self.client:raise RuntimeError('Chalk credentials unavailable: '+str(self.init_error))
            self.pending=self.pool.submit(self._query,labels,as_of,hour,qid)
            priors=self.pending.result(timeout=self.config.chalk_timeout_s)
            self.cache,self.cache_key,self.cache_time=priors,key,time.monotonic()
        except Exception as e:
            error=type(e).__name__;self.cache=None;self.down_until=time.monotonic()+5
            if self.pending:self.pending.cancel()
        self.last=dict(query_id=qid,latency_ms=round((time.perf_counter()-started)*1000,1),
            result='success' if priors else 'fallback',api_attempted=attempted,error=error,hour_bucket=hour,priors=priors,cloud_evidence=self.cloud_evidence if priors else None)
        log('chalk_queries.jsonl',**self.last)
        if strict and priors is None:raise RuntimeError('Live Chalk query failed: '+str(error))
        return priors

    def close(self):self.pool.shutdown(wait=False,cancel_futures=True)

def rank(candidates,priors,weight):
    """(id,label,gaze score) -> rows, ranked only among gaze-supported objects."""
    rows=[]
    for oid,label,gaze in candidates:
        if not math.isfinite(gaze) or gaze<0:raise ValueError('Invalid gaze score')
        p=priors.get(label,0) if priors else None
        value=gaze*(p**weight) if p is not None else gaze
        rows.append(dict(id=oid,label=label,gaze=gaze,prior=p,score=value))
    total=sum(r['score'] for r in rows)
    for row in rows:row['probability']=row['score']/total if total else 0
    return sorted(rows,key=lambda r:r['score'],reverse=True)

class RoutineService:
    def __init__(self,base,client):
        self.base=base;self.client=client;self.user_id=base.user_id;self.params=base.params
        self.last_query={};self.rows=[]
    def predict(self,objects,gaze,history,t_now,corrections=()):
        from look_intent.scoring import gaze_shares,Decision,current_dwell
        default=self.base.predict(objects,gaze,history,t_now,corrections=corrections)
        self.last_query=self.base.last_query;self.rows=[]
        shares=gaze_shares(gaze,objects,t_now,self.params)
        supported=[o for o in objects if shares.get(o.id,0)>0]
        if len(supported)<2:return default
        priors=self.client.query([o.cls for o in supported])
        self.rows=rank([(o.id,o.cls,shares[o.id]) for o in supported],priors,self.client.config.routine_weight)
        if not priors:return default
        top=self.rows[0];obj=next(o for o in supported if o.id==top['id'])
        self.last_query={**self.client.last,'source':'chalk_routine'}
        eligible=(top['probability']>=self.params.threshold and shares[obj.id]>=self.params.min_share
                  and current_dwell(gaze,obj,t_now,self.params)>=self.params.min_dwell_s)
        return Decision(obj.id,top['probability'],eligible,{r['id']:r['probability'] for r in self.rows})
    def close(self):self.client.close();self.base.close()
