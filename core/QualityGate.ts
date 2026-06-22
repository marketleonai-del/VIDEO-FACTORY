/**
 * QualityGate.ts — 120 门质量闸（每条产出都过同一套标准）
 *
 * 维度与分值（继承 ugc-creative-amplifier）：
 *   内容30 + 真实25 + 多样20 + 合规20 + 闭环15 + 技术10 = 120
 * 及格线 100；<100 自动重修。矩阵与单条用同一闸（不存在"矩阵低标准"）。
 */

/** 各维度评分输入（0~满分） */
export interface QualityInput {
  content: number; // 0-30：钩子3秒成立/卖点可视化/货对得上板
  authenticity: number; // 0-25：信任点真素材/无恐怖谷/音色统一@voice1
  diversity: number; // 0-20：段内≤15%、跨账号≤20%、钩子族不同
  compliance: number; // 0-20：AI标识/分区清单/零禁用词/功效合规
  loop: number; // 0-15：带WinScore/预留actual回填
  tech: number; // 0-10：@image1锁/@voice1锁/模型无关提示词/拼接衔接
}

export interface QualityResult {
  score: number;
  pass: boolean;
  /** 未达标的维度提示 */
  failures: string[];
  detail: Required<QualityInput>;
}

const MAX: Required<QualityInput> = {
  content: 30,
  authenticity: 25,
  diversity: 20,
  compliance: 20,
  loop: 15,
  tech: 10,
};

const LABEL: Record<keyof QualityInput, string> = {
  content: "内容",
  authenticity: "真实",
  diversity: "多样",
  compliance: "合规",
  loop: "闭环",
  tech: "技术",
};

export const PASS_LINE = 100;

export class QualityGate {
  /** 评一条 */
  evaluate(input: QualityInput): QualityResult {
    const detail = { ...input };
    const failures: string[] = [];
    let score = 0;
    (Object.keys(MAX) as Array<keyof QualityInput>).forEach((k) => {
      const v = Math.max(0, Math.min(input[k], MAX[k]));
      score += v;
      // 单维低于 70% 视为薄弱项，提示重修方向
      if (v < MAX[k] * 0.7) failures.push(`${LABEL[k]}(${v}/${MAX[k]}) 偏弱`);
    });
    return { score, pass: score >= PASS_LINE, failures, detail };
  }

  /** 矩阵逐条过闸：返回每条结果 + 是否全部通过 */
  evaluateBatch(inputs: QualityInput[]): { allPass: boolean; results: QualityResult[] } {
    const results = inputs.map((i) => this.evaluate(i));
    return { allPass: results.every((r) => r.pass), results };
  }
}
