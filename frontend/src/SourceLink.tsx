import type {ReactNode} from 'react';
import {safeSourceLink} from './safe-links';
type Props={href?:string;children?:ReactNode;className?:string;dir?:'ltr'|'rtl'|'auto';title?:string;'aria-label'?:string;target?:string;rel?:string};
/** Rejected destinations retain their readable label without a navigable anchor. */
export function SourceLink({href,children,className,dir,title,'aria-label':label}:Props){
 const safe=safeSourceLink(href);
 return safe?<a href={safe} className={className} dir={dir} title={title} aria-label={label} target="_blank" rel="noopener noreferrer">{children}</a>:<span className={className} dir={dir} title={title} aria-label={label}>{children}</span>;
}
