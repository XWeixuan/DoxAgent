"""One official X Filtered Stream, owned by the existing Collector."""
import asyncio
import json
from datetime import datetime
from time import monotonic
from urllib.parse import unquote
import httpx
from .schema import SourceDefinition, SourceKind, ContentEnrichmentMode, RawMessageInput, utc_now, canonical_json, sha256_text, new_id

SOURCE_ID='x_official_posts'
API='https://api.x.com/2'
TAG='doxagent:official:v1'
ACCOUNTS={'INTC':['intel'],'MU':['MicronTech'],'BE':['Bloom_Energy'],'COHR':['CoherentCorp'],
 'NBIS':['nebiusai'],'CEG':['ConstellationEG'],'RKLB':['RocketLab'],'SNOW':['Snowflake'],
 'ONDS':['OndasHoldings'],'IONQ':['IonQ_Inc']}


def initial_source():
    return SourceDefinition(source_id=SOURCE_ID,display_name='X Official Account Posts',kind=SourceKind.API,
        adapter_ref='builtin:x_official_stream@1',scheduler_group=SOURCE_ID,
        entry_url=API+'/tweets/search/stream',content_language='en',
        content_enrichment_mode=ContentEnrichmentMode.SKIP,
        default_parameters={'http_route':'us-standard-5'})


def parse_post(event, accounts):
    data=event.get('data',{})
    users={u['id']:u['username'].lower() for u in event.get('includes',{}).get('users',[])}
    username=users.get(data.get('author_id'))
    if username not in accounts:return None
    text=(data.get('note_tweet') or {}).get('text') or data.get('text')
    if not text or not data.get('id') or not data.get('created_at'):return None
    # Full API-native post; do not follow the URLs or fetch quoted article bodies.
    entities=(data.get('note_tweet') or {}).get('entities') or data.get('entities',{})
    for url in entities.get('urls',[]):
        if url.get('expanded_url'):text=text.replace(url['url'],url['expanded_url'])
    stable_id=(data.get('edit_history_tweet_ids') or [data['id']])[0]
    return RawMessageInput(title=text.splitlines()[0][:240],body=text,
        url=f"https://x.com/{username}/status/{stable_id}",source='X @'+username,
        external_id=stable_id,source_item_key=stable_id,
        published_at=datetime.fromisoformat(data['created_at'].replace('Z','+00:00')),
        raw_payload=event,metadata={'provider':'x_official','x_author_id':data['author_id'],
        'x_username':username,'language':data.get('lang'),'body_not_applicable':True,
        'publication_time_precision':'second','identity_evidence':{'id_kind':'stable_article','url_kind':'article'},
        'v2_body_completion':{'network_attempted':False,'outcome':'NOT_APPLICABLE'}})


class OfficialStream:
    def __init__(self,collector,*,client=None):
        self.owner=collector
        self.client=client
        self.session=None
        self.received=0
        self.health={}

    def roster(self):
        c=self.owner.config;now=utc_now();result={}
        source=c.sources.get(SOURCE_ID)
        if not source or not source.enabled:return source,result
        for b in c.bindings.values():
            if b.source_id!=SOURCE_ID or not c.allowed(b,now,control=self.owner._control):continue
            found=c.terms.get(b.ticker)
            for account in found[1].x_accounts if found else []:
                result.setdefault(account.lower(),[]).append(b)
        return source,result

    async def reconcile(self,accounts):
        response=await self.client.get(API+'/tweets/search/stream/rules',timeout=10)
        response.raise_for_status()
        rows=response.json().get('data',[])
        # Ten individual rules fit the standard rule budget and stay below length limits.
        expected={f'from:{a}':TAG+':'+a for a in sorted(accounts)}
        own=[r for r in rows if r.get('tag','').startswith(TAG+':')]
        existing={(r['value'],r.get('tag')) for r in own}
        add=[{'value':v,'tag':t} for v,t in expected.items() if (v,t) not in existing]
        if add:
            r=await self.client.post(API+'/tweets/search/stream/rules',json={'add':add},timeout=10);r.raise_for_status()
            if r.json().get('errors'):raise RuntimeError('x_rule_add_rejected')
        obsolete=[r['id'] for r in own if expected.get(r['value'])!=r.get('tag')]
        if obsolete:
            r=await self.client.post(API+'/tweets/search/stream/rules',json={'delete':{'ids':obsolete}},timeout=10);r.raise_for_status()
            if r.json().get('errors'):raise RuntimeError('x_rule_delete_rejected')
        # Other applications' rules are never deleted. Incoming authors are checked locally.

    async def status(self,state,**values):
        self.health.update(dict(state=state,at=utc_now().isoformat(),session=self.session,received=self.received,**values))
        await asyncio.to_thread(self.owner.store.meta,'x_official_stream',dict(self.health))

    async def capture(self,event):
        source,roster=self.roster()  # Recheck revocation/term changes at receive time.
        message=parse_post(event,roster)
        if message is None:return
        targets=roster[message.metadata['x_username']]
        observed=utc_now().isoformat()
        terms=self.owner.config.terms
        payload={'source_id':SOURCE_ID,'delivery_mode':'production','bootstrap':False,
            'targets':[{'binding_id':b.binding_id,'ticker':b.ticker,'terms_revision':terms[b.ticker][0]} for b in targets],
            'result':{'messages':[message.model_dump(mode='json')]},'admission':{'observed_at':observed},
            'input_keys':[sha256_text(canonical_json([SOURCE_ID,message.external_id,message.body,sorted(b.binding_id for b in targets)]))]}
        # Same durable captures and bridge as polled sources. No fabricated polling receipt.
        await asyncio.to_thread(self.owner.store.capture_stream,source,targets,payload,observed,self.session)
        self.received+=1
        await self.status('CONNECTED',last_post_id=message.external_id,last_post_at=observed)

    async def consume(self):
        params={'tweet.fields':'created_at,author_id,lang,note_tweet,entities,referenced_tweets,edit_history_tweet_ids',
                'expansions':'author_id','user.fields':'username'}
        async with self.client.stream('GET',API+'/tweets/search/stream',params=params) as response:
            response.raise_for_status()
            self.session=new_id('x-stream')
            await self.status('CONNECTED',connected_at=utc_now().isoformat(),accounts=sorted(self.roster()[1]),error=None,http_status=200)
            async for line in response.aiter_lines():
                if not line.strip():
                    await self.status('CONNECTED',provider_keepalive_at=utc_now().isoformat())
                    continue  # Provider heartbeat, no news/poll fabricated.
                event=json.loads(line)
                if 'data' in event:await self.capture(event)
                elif event.get('errors') or event.get('connection_issue'):
                    raise RuntimeError('x_stream_provider_disconnect')
        raise ConnectionError('x_stream_eof')

    async def run(self,stop):
        token=getattr(self.owner.settings,'x_bearer_token',None)
        if not token:
            await self.status('CREDENTIAL_MISSING');return
        owned=self.client is None
        if owned:
            from .http_routes import HTTP_EGRESSES
            source=self.owner.config.sources.get(SOURCE_ID)
            route=source.default_parameters.get('http_route','us-standard-5') if source else 'us-standard-5'
            self.client=httpx.AsyncClient(proxy=HTTP_EGRESSES.get(route),headers={'Authorization':'Bearer '+unquote(token)},
            timeout=httpx.Timeout(connect=10,read=90,write=10,pool=10),trust_env=False)
        failures=0
        try:
            while not stop.is_set():
                source,roster=self.roster()
                if not roster:
                    if self.health.get('state')!='IDLE':await self.status('IDLE')
                    await asyncio.sleep(1);continue
                signature=tuple((a,tuple((b.binding_id,b.version) for b in bs)) for a,bs in sorted(roster.items()))
                consumer=None
                try:
                    await self.status('CONNECTING',accounts=sorted(roster))
                    await self.reconcile(roster)
                    consumer=asyncio.create_task(self.consume())
                    current=roster
                    healthy_since=monotonic()
                    while not stop.is_set():
                        await asyncio.wait({consumer},timeout=1)
                        if consumer.done():await consumer
                        _,current=self.roster()
                        fresh=tuple((a,tuple((b.binding_id,b.version) for b in bs)) for a,bs in sorted(current.items()))
                        if fresh!=signature:
                            await self.reconcile(current)
                            signature=fresh
                            await self.status(self.health.get('state','CONNECTING'),accounts=sorted(current))
                            if not current:break
                        if monotonic()-healthy_since>=60:failures=0
                except asyncio.CancelledError:raise
                except Exception as exc:
                    if consumer:
                        consumer.cancel();await asyncio.gather(consumer,return_exceptions=True)
                        consumer=None
                    failures+=1
                    code=exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else None
                    delay=300 if code in {401,402,403} else min(60,2**min(failures,6))
                    if code==429:
                        try:delay=max(delay,min(900,int(exc.response.headers.get('x-rate-limit-reset','0'))-int(utc_now().timestamp())))
                        except ValueError:delay=60
                    await self.status('RETRY_WAIT',error=type(exc).__name__,http_status=code,retry_seconds=delay,gap_started_at=utc_now().isoformat())
                    try:await asyncio.wait_for(stop.wait(),delay)
                    except TimeoutError:pass
                finally:
                    if consumer:
                        consumer.cancel();await asyncio.gather(consumer,return_exceptions=True)
        finally:
            if owned:await self.client.aclose()


class StreamOnlyAdapter:
    async def poll(self,context):
        raise RuntimeError('X official posts use the Collector Filtered Stream; manual polling is not supported')
