import assert from 'node:assert/strict';
import test from 'node:test';
import {decisions,duration} from '../frigate_storage_manager/fsm/static/overview.mjs';

test('no selected events and no bookmarks are described without implying protection was tested',()=>{
  const result=decisions({counts:{recordings:2000,event:0,reviewsegment:0},preserved:{event:80,bookmarks:0}});
  assert.match(result.removal,/No events or review items/);
  assert.match(result.bookmarks,/No bookmarked events/);
  assert.match(result.bookmarks,/does not demonstrate/);
});
test('selected event/review counts and actual bookmarks appear in the explanation',()=>{
  const result=decisions({counts:{event:2,reviewsegment:1},preserved:{bookmarks:3}});
  assert.match(result.removal,/2 events and 1 review item would/);
  assert.match(result.bookmarks,/3 bookmarked events stay/);
});
test('footage duration handles short clips and long camera totals',()=>{
  assert.equal(duration(20),'20 sec');
  assert.equal(duration(90),'1 min');
  assert.equal(duration(3661),'1 hr 1 min');
});
