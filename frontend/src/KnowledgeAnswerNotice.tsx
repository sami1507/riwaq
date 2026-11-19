import { CircleAlert } from 'lucide-react';
import type { Lang } from './ui';

export default function KnowledgeAnswerNotice({lang,id,className=''}:{lang:Lang;id?:string;className?:string}){
 return <p id={id} className={`knowledge-answer-notice ${className}`} role="note"><CircleAlert size={15} aria-hidden="true"/><span>{lang==='ar'?'الإجابة من المعرفة تجريبية: قد يفسّر النموذج المقاطع خطأ. راجع الشواهد الأصلية.':'Knowledge-grounded answers are experimental: the model can misread excerpts. Review the original evidence.'}</span></p>;
}
