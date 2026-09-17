import { FC } from 'react'

const Idle: FC = () => {
  return (
    <div className="flex items-center justify-center">
      <svg
        width="280"
        height="200"
        viewBox="0 0 220 160"
        fill="none"
        aria-hidden
      >
        <line x1="28" y1="128" x2="192" y2="128" stroke="#c4a882" strokeWidth="1.5" />
        <rect x="78" y="62" width="72" height="52" rx="2" stroke="#7a746a" strokeWidth="1.4" fill="#fbf8f2" />
        <line x1="92" y1="78" x2="136" y2="78" stroke="#d6cfc3" strokeWidth="1.2" />
        <line x1="92" y1="90" x2="128" y2="90" stroke="#d6cfc3" strokeWidth="1.2" />
        <line x1="92" y1="102" x2="118" y2="102" stroke="#d6cfc3" strokeWidth="1.2" />
        <path
          d="M48 96c0-10 8-18 18-18s18 8 18 18v20H48V96z"
          stroke="#7a746a"
          strokeWidth="1.4"
          fill="#f3efe6"
        />
        <ellipse cx="66" cy="78" rx="10" ry="4" stroke="#7a746a" strokeWidth="1.4" fill="#fbf8f2" />
        <path d="M58 88c6-4 10-4 16 0" stroke="#c4a882" strokeWidth="1.2" />
      </svg>
    </div>
  )
}

export default Idle
