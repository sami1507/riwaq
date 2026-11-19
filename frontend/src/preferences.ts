import { useEffect, useState } from 'react';
export type Theme='light'|'dark'|'system';
export type Digits='latn'|'arab';
export type DateStyle='local'|'iso';
export function useAppearance(){
 const [theme,setTheme]=useState<Theme>(()=>{const v=localStorage.getItem('riwaq.v1.theme');return v==='dark'||v==='light'?v:'system';});
 const [systemDark,setSystemDark]=useState(()=>matchMedia('(prefers-color-scheme: dark)').matches);
 const [digits,setDigits]=useState<Digits>(()=>localStorage.getItem('riwaq.v1.digits')==='arab'?'arab':'latn');
 const [dateStyle,setDateStyle]=useState<DateStyle>(()=>localStorage.getItem('riwaq.v1.dates')==='iso'?'iso':'local');
 const dark=theme==='dark'||(theme==='system'&&systemDark);
 useEffect(()=>{const q=matchMedia('(prefers-color-scheme: dark)');const update=()=>setSystemDark(q.matches);q.addEventListener('change',update);return()=>q.removeEventListener('change',update);},[]);
 useEffect(()=>{document.documentElement.dataset.theme=dark?'dark':'light';document.documentElement.style.colorScheme=dark?'dark':'light';document.querySelector('meta[name="theme-color"]')?.setAttribute('content',getComputedStyle(document.documentElement).getPropertyValue('--color-background').trim());document.querySelector('link[data-riwaq-icon]')?.setAttribute('href',dark?'/brand/riwaq-symbol-dark.svg':'/brand/riwaq-symbol-light.svg');localStorage.setItem('riwaq.v1.theme',theme);},[dark,theme]);
 useEffect(()=>{localStorage.setItem('riwaq.v1.digits',digits);localStorage.setItem('riwaq.v1.dates',dateStyle);window.dispatchEvent(new Event('riwaq-format-change'));},[digits,dateStyle]);
 return {theme,setTheme,dark,digits,setDigits,dateStyle,setDateStyle};
}
export function formatNumber(value:number,lang:string){return new Intl.NumberFormat(lang,{numberingSystem:localStorage.getItem('riwaq.v1.digits')==='arab'?'arab':'latn'}).format(value);}
export function formatDate(value:string,lang:string){const date=new Date(value);if(Number.isNaN(date.valueOf()))return value;return localStorage.getItem('riwaq.v1.dates')==='iso'?date.toISOString().slice(0,16).replace('T',' ')+' UTC':new Intl.DateTimeFormat(lang,{dateStyle:'medium',timeStyle:'short',numberingSystem:localStorage.getItem('riwaq.v1.digits')==='arab'?'arab':'latn'}).format(date);}
