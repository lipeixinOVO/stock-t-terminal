def plot_daily_chart(df, symbol_name, latest, uirevision_key=0):
    """日线图：三面板（K线、成交量、MACD）
       主图（K线）支持框选放大/拖动；
       副图（成交量/MACD）完全锁定，仅跟随主图的 X 轴同步
    """
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.6, 0.2, 0.2])
    
    fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='日K',
                                 increasing_line_color='#ff3333', decreasing_line_color='#00cc66', line=dict(width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA5'], mode='lines', name='MA5', line=dict(color='#ffffff', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA10'], mode='lines', name='MA10', line=dict(color='#ffff00', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], mode='lines', name='MA20', line=dict(color='#ff00ff', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA30'], mode='lines', name='MA30', line=dict(color='#00ff00', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA250'], mode='lines', name='年线', line=dict(color='#00ccff', width=1.5, dash='dash')), row=1, col=1)
    
    buy_s = df[df['Signal'] == 1]
    sell_s = df[df['Signal'] == -1]
    if not buy_s.empty:
        fig.add_trace(go.Scatter(x=buy_s['Date'], y=buy_s['Low']*0.98, mode='markers', name='买点', 
                                 marker=dict(symbol='triangle-up', size=16, color='#ff0000', line=dict(width=2, color='white'))), row=1, col=1)
    if not sell_s.empty:
        fig.add_trace(go.Scatter(x=sell_s['Date'], y=sell_s['High']*1.02, mode='markers', name='卖点', 
                                 marker=dict(symbol='triangle-down', size=16, color='#00ff00', line=dict(width=2, color='white'))), row=1, col=1)
    
    fig.add_hline(y=latest['Close'], line_dash="dot", line_color="#888888", line_width=1.5, row=1, col=1,
                  annotation_text=f"{latest['Close']:.3f}", annotation_position="left", 
                  annotation_font=dict(color="#f0f2f6", size=12))
    
    vol_colors = ['#ff3333' if c >= o else '#00cc66' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df['Date'], y=df['Volume'], name='成交量', marker_color=vol_colors), row=2, col=1)
    
    colors = ['#ff3333' if c >= o else '#00cc66' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df['Date'], y=df['MACD'], name='MACD', marker_color=colors), row=3, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['DIFF'], mode='lines', name='DIFF', line=dict(color='#ffffff', width=1.5)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['DEA'], mode='lines', name='DEA', line=dict(color='#ffaa00', width=1.5)), row=3, col=1)
    fig.add_hline(y=0, line_width=1, line_dash="dash", line_color="#888888", row=3, col=1)
    
    fig.update_layout(
        template="plotly_dark", height=650, xaxis_rangeslider_visible=False, 
        hovermode="x unified", dragmode='zoom',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), 
        margin=dict(t=50, l=10, r=10, b=10),
        uirevision=uirevision_key
    )
    
    # ===== 关键修复 =====
    # 主图（K线面板）：允许框选缩放 / 拖动
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], fixedrange=False, row=1, col=1)
    fig.update_yaxes(fixedrange=False, row=1, col=1)
    # 副图：成交量面板 —— 完全锁定
    fig.update_xaxes(fixedrange=True, row=2, col=1)
    fig.update_yaxes(fixedrange=True, row=2, col=1)
    # 副图：MACD面板 —— 完全锁定
    fig.update_xaxes(fixedrange=True, row=3, col=1)
    fig.update_yaxes(fixedrange=True, row=3, col=1)
    
    return fig
