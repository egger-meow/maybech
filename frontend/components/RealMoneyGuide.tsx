import { CheckCircle2, KeyRound, LockKeyhole, ServerCog, ShieldCheck, TriangleAlert } from "lucide-react";

const steps = [
  {
    icon: KeyRound,
    title: "1. 分離 Demo 與正式金鑰",
    detail: "先用 DEMO_OKX_* 與 OKX_FLAG=1 完成驗證。正式環境才使用 OKX_* 與 OKX_FLAG=0；金鑰只開交易必要權限、不要開提幣權限，並設定 OKX IP 白名單。",
  },
  {
    icon: ServerCog,
    title: "2. 確認 OKX 帳戶模式",
    detail: "永續合約帳戶必須可交易衍生品，且部位模式為 net_mode。現貨帳戶等級與 long/short mode 都會被啟動檢查拒絕。",
  },
  {
    icon: ShieldCheck,
    title: "3. 先在 Dry-run 完成策略與風險上限",
    detail: "設定每筆名目金額、總曝險與最大槓桿；檢查策略口數、最大滑價與至少一個方向正確的停損。策略資料儲存在 SQLite，不放進 .env。",
  },
  {
    icon: LockKeyhole,
    title: "4. 分兩層武裝",
    detail: "先以 --live 啟動，再明確設定 MAYBECH_ARM_ORDERS=1 並通過 preflight。之後仍須個別啟用策略及另外確認開啟進場閘門；每次重新啟動都會再次關閉進場。",
  },
  {
    icon: CheckCircle2,
    title: "5. 逐項確認成交生命週期",
    detail: "先用 OKX Demo 驗證進場、保護單、部分減倉、平倉與重新啟動追補。正式環境只以可承受的最小口數分階段驗證，並檢查沒有殘留委託或 Algo。",
  },
];

export default function RealMoneyGuide() {
  return (
    <details className="panel real-money-guide">
      <summary className="guide-summary">
        <div><h2><TriangleAlert size={21} /> 從模擬走向真實資金</h2><p>需要設定實盤時再展開；畫面不提供一鍵武裝。</p></div>
        <span className="badge danger">高風險操作</span>
      </summary>
      <div className="guide-content">
        <div className="arm-zero"><LockKeyhole size={22} /><div><strong><code>MAYBECH_ARM_ORDERS=0</code> 代表不會放置任何真實委託</strong><p>這是預設值。匯入模組、開啟前端或只啟動一般 API 都不會自行武裝。</p></div></div>
        <ol className="guide-steps">
          {steps.map(({ icon: Icon, title, detail }) => <li key={title}><Icon size={21} /><div><strong>{title}</strong><p>{detail}</p></div></li>)}
        </ol>
        <div className="guide-footer">完整命令、環境變數與驗證工具請依照 <code>docs/deployment.md</code>。不要把 API 金鑰貼進瀏覽器、文件、提交紀錄或聊天內容。</div>
      </div>
    </details>
  );
}
