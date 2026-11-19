import type {HistoryCompaction} from './api';

// Declared history compaction: earlier assistant replies were abbreviated or
// omitted so the conversation fits the model's context; user turns never are.
function ranges(turns:number[],sep:string):string{
 const out:string[]=[];let start=turns[0],prev=turns[0];
 for(const n of [...turns.slice(1),NaN]){if(n===prev+1){prev=n;continue;}out.push(start===prev?`${start}`:`${start}–${prev}`);start=n;prev=n;}
 return out.join(sep);
}

export default function ContextNotice({context,lang}:{context:HistoryCompaction;lang:'ar'|'en'}){
 const turns=(action:'abbreviated'|'omitted')=>context.affected_turns.filter(t=>t.action===action).map(t=>t.turn).sort((a,b)=>a-b);
 const shortened=turns('abbreviated'),omitted=turns('omitted');
 if(!shortened.length&&!omitted.length)return null;
 const ar=(list:number[])=>`${list.length===1?'الدور':'الأدوار'} ${ranges(list,'، ')}`;
 const en=(list:number[])=>`${list.length===1?'turn':'turns'} ${ranges(list,', ')}`;
 const text=lang==='ar'
  ?'لتتسع المحادثة لسعة النموذج: '+[shortened.length?`اختُصرت ردود رواق السابقة في ${ar(shortened)}`:'',omitted.length?(shortened.length?`وأُخرجت ردوده من السياق في ${ar(omitted)}`:`أُخرجت ردود رواق السابقة من السياق في ${ar(omitted)}`):''].filter(Boolean).join(' ')+`. رسائلك كلها أُرسلت كاملة${context.latest_assistant_reply_full?'، وكذلك آخر رد':''}.`
  :"To fit the model's context: "+[shortened.length?`earlier Riwaq replies were abbreviated in ${en(shortened)}`:'',omitted.length?`${shortened.length?'and ':''}left out in ${en(omitted)}`:''].filter(Boolean).join(' ')+`. All your messages were sent in full${context.latest_assistant_reply_full?', as was the latest reply':''}.`;
 return <p className="knowledge-answer-notice context-notice" role="note">{text}</p>;
}
