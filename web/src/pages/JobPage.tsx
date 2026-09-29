import { Link, useParams } from "react-router-dom";
import JobView from "../components/JobView";

// 실행 창을 브라우저 새 창으로 분리한 화면. 링크를 받은 다른 사람도 볼 수 있다(취소는 요청자만).
export default function JobPage() {
  const { id = "" } = useParams();
  return (
    <div className="page">
      <Link to="/">← 처음으로</Link>
      <JobView jobId={id} />
    </div>
  );
}
