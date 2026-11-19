import {useEffect,useRef,useState} from 'react';
import type {Message} from './api';

/** Follow streaming only while the reader remains near the end. */
export function useConversationScroll(messages:Message[],generating:boolean,conversation:string){
 const viewport=useRef<HTMLDivElement>(null),follow=useRef(true),[unread,setUnread]=useState(false);
 const jump=()=>{follow.current=true;setUnread(false);const element=viewport.current;if(element)element.scrollTo({top:element.scrollHeight,behavior:'instant'});};
 useEffect(()=>{follow.current=true;setUnread(false);},[conversation]);
 // An empty conversation shows the welcome screen: start at its top, never at the end,
 // or the heading is scrolled out of view whenever the welcome is taller than the viewport.
 useEffect(()=>{if(!messages.length){viewport.current?.scrollTo({top:0,behavior:'instant'});return;}if(follow.current)jump();else setUnread(true);},[messages,generating]);
 const onScroll=()=>{const el=viewport.current;if(!el)return;follow.current=el.scrollHeight-el.scrollTop-el.clientHeight<100;if(follow.current)setUnread(false);};
 return {viewport,onScroll,unread,jump};
}
