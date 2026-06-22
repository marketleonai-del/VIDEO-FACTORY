/**
 * Storage.ts — 产物存储抽象（音频/视频/成片）。本地实现；生产换 S3/OSS/Cloudinary 实现同接口即可。
 */
import * as fs from "node:fs";
import * as path from "node:path";

export interface Storage {
  save(key: string, data: Buffer | string): Promise<string>;
  url(key: string): string;
}

export class LocalStorage implements Storage {
  constructor(private dir = process.env.UVG_STORAGE_DIR || "./.uvg-artifacts") {}
  async save(key: string, data: Buffer | string): Promise<string> {
    const p = path.join(this.dir, key);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, data);
    return this.url(key);
  }
  url(key: string): string {
    return `file://${path.resolve(this.dir, key)}`;
  }
}
