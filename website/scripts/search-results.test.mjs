import { test } from 'node:test';
import assert from 'node:assert/strict';
import { conciseSearchResults } from '../lib/search-results.ts';
const result = (id, type, content, url) => ({ id, type, content, url });

test('matching guide titles outrank repeated reference mentions without mutating results', () => {
  const input = [result('ref','page','CLI reference','/docs/reference/cli'),
    ...Array.from({length:20},(_,i)=>result('text'+i,'text','backup details','/docs/reference/cli#'+i)),
    result('guide','page','<mark>Backup</mark> and recovery','/docs/guides/backup-recovery'),
    result('heading','heading','Export a backup','/docs/guides/backup-recovery#export')];
  const before=JSON.stringify(input);
  const output=conciseSearchResults(input,'backup');
  assert.equal(output[0].id,'guide');
  assert.equal(output.filter(x=>x.url.startsWith('/docs/reference/cli')).length,3);
  assert.equal(JSON.stringify(input),before);
});

test('prefers concise heading hits and bounds the initial page list', () => {
  const input=Array.from({length:12},(_,i)=>[
    result('page'+i,'page','Page '+i,'/docs/'+i),
    result('text'+i,'text','Very long paragraph','/docs/'+i+'#paragraph'),
    result('heading'+i,'heading','Section','/docs/'+i+'#section')]).flat();
  const output=conciseSearchResults(input,'Page');
  assert.equal(output.filter(x=>x.type==='page').length,8);
  assert.equal(output.some(x=>x.type==='text'),false);
  assert.deepEqual(conciseSearchResults([],''),[]);
});
