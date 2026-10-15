#!/bin/bash
# đo tốc độ sinh: ollama native API (think=false)
M=${1:-qwen3.5:4b}
for i in 1 2 3; do
curl -s localhost:11434/api/chat -d "{\"model\":\"$M\",\"stream\":false,\"think\":false,\"options\":{\"num_ctx\":8192},\"messages\":[{\"role\":\"user\",\"content\":\"Viết 150 từ giới thiệu về chủ quyền số cho doanh nghiệp Việt Nam.\"}]}" | python3 -c "
import sys,json;d=json.load(sys.stdin)
print('load %.1fs prompt %d tok @ %.0f t/s | gen %d tok @ %.1f t/s'%(d['load_duration']/1e9,d['prompt_eval_count'],d['prompt_eval_count']/(d['prompt_eval_duration']/1e9),d['eval_count'],d['eval_count']/(d['eval_duration']/1e9)));
print(d['message']['content'][:200].replace('\n',' ')) if len(sys.argv)>1 else 0" $i
done
ollama ps
nvidia-smi --query-gpu=memory.used --format=csv
