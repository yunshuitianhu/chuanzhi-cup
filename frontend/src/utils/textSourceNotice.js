// Error bodies are diagnostic data, not copy to display in the application.
export function textSourceNotice(error) {
  if (error?.status === 409) return {
    title: '这段引用需要重新选择',
    message: '你之前引用的文字对应较早的项目或分析结果。当前结果已更新，请重新选中需要提问的文字，再点击“添加到对话”或“在侧边聊天中提问”。',
    retryable: false,
  }
  if (error?.status === 404) return {
    title: '暂时找不到之前引用的文字',
    message: '这条“核对来源”链接已失效，可能发生在重启服务或恢复项目后。请在当前页面重新选中文字，再添加到对话或侧边提问。',
    retryable: false,
  }
  if (error?.status === 422) return {
    title: '这条文字引用链接无法使用',
    message: '链接不完整或格式不正确。请回到需要提问的内容，重新选中文字。',
    retryable: false,
  }
  return {
    title: '暂时无法打开文字引用',
    message: '引用内容暂时未能加载。你可以重试，或关闭提示后重新选中文字提问。',
    retryable: true,
  }
}

export function withoutTextSource(route) {
  const {text_context, ...query} = route.query
  return {path: route.path, query, hash: route.hash}
}
