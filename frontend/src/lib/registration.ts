type ChoiceOption = {
  value: string
  label: string
}

export function hasReusableOAuthBrowser(config: { chrome_user_data_dir?: string; chrome_cdp_url?: string }) {
  return Boolean(config.chrome_user_data_dir?.trim() || config.chrome_cdp_url?.trim())
}

function getOptionLabel(value: string, options: ChoiceOption[] = []) {
  return options.find(item => item.value === value)?.label || value
}

export function pickOAuthExecutor(
  supportedExecutors: string[],
  preferredExecutor: string,
  reusableBrowser: boolean,
) {
  if (supportedExecutors.includes(preferredExecutor) && preferredExecutor !== 'protocol') {
    return preferredExecutor
  }
  if (reusableBrowser && supportedExecutors.includes('headless')) {
    return 'headless'
  }
  if (supportedExecutors.includes('headed')) {
    return 'headed'
  }
  if (supportedExecutors.includes('headless')) {
    return 'headless'
  }
  return supportedExecutors[0] || ''
}

export function buildRegistrationOptions(platformMeta: any) {
  const supportedModes: string[] = platformMeta?.supported_identity_modes || []
  const supportedOAuth: string[] = platformMeta?.supported_oauth_providers || []
  const identityModeOptions: ChoiceOption[] = platformMeta?.supported_identity_mode_options || []
  const oauthProviderOptions: ChoiceOption[] = platformMeta?.supported_oauth_provider_options || []
  const options: Array<{
    key: string
    label: string
    description: string
    identityProvider: string
    oauthProvider: string
  }> = []

  if (supportedModes.includes('mailbox')) {
    options.push({
      key: 'mailbox',
      label: getOptionLabel('mailbox', identityModeOptions),
      description: `使用${getOptionLabel('mailbox', identityModeOptions)}自动收验证码并完成注册`,
      identityProvider: 'mailbox',
      oauthProvider: '',
    })
  }

  if (supportedModes.includes('manual_phone')) {
    options.push({
      key: 'manual_phone',
      label: getOptionLabel('manual_phone', identityModeOptions),
      description: '系统租用手机号并等待短信，图形验证与发送短信由人工在普通浏览器中完成',
      identityProvider: 'manual_phone',
      oauthProvider: '',
    })
  }

  if (supportedModes.includes('oauth_browser')) {
    supportedOAuth.forEach((provider: string) => {
      const providerLabel = getOptionLabel(provider, oauthProviderOptions)
      options.push({
        key: `oauth:${provider}`,
        label: providerLabel,
        description: `使用 ${providerLabel} 账号自动创建平台账号`,
        identityProvider: 'oauth_browser',
        oauthProvider: provider,
      })
    })
  }

  return options
}

export function buildExecutorOptions(
  identityProvider: string,
  supportedExecutors: string[],
  reusableBrowser: boolean,
  executorOptions: ChoiceOption[] = [],
) {
  return supportedExecutors.map((executor) => {
    const option = {
      value: executor,
      label: getOptionLabel(executor, executorOptions),
      description: '',
      disabled: false,
      reason: '',
    }

    if (executor === 'protocol') {
      option.description = '不打开浏览器，直接通过协议流程自动注册'
      if (identityProvider === 'manual_phone') {
        option.disabled = true
        option.reason = '\u624b\u673a\u53f7\u6d41\u7a0b\u9700\u8981\u4eba\u5de5\u5728\u666e\u901a\u6d4f\u89c8\u5668\u4e2d\u5b8c\u6210\u56fe\u5f62\u9a8c\u8bc1\u548c\u53d1\u9001\u77ed\u4fe1'
        return option
      }
      if (identityProvider !== 'mailbox') {
        option.disabled = true
        option.reason = '第三方账号注册必须通过浏览器自动化完成'
      }
      return option
    }

    if (executor === 'headless') {
      option.description = identityProvider === 'mailbox'
        ? '浏览器在后台自动执行，界面不可见'
        : '复用本机浏览器登录态，在后台自动完成第三方登录'
      if (identityProvider === 'oauth_browser' && !reusableBrowser) {
        option.disabled = true
        option.reason = '需要先在全局配置里填写 Chrome Profile 路径或 Chrome CDP 地址'
      }
      return option
    }

    if (executor === 'manual_assisted') {
      option.description = '系统租号并等待短信，需要人工在普通浏览器中完成图形验证和发送短信'
      if (identityProvider !== 'manual_phone') {
        option.disabled = true
        option.reason = '人工辅助执行器仅用于手机号注册'
      }
      return option
    }

    option.description = '会打开浏览器窗口，但系统仍自动执行，无需额外交互'
    return option
  })
}
