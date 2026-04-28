# Streamlit Data Dashboard

A Streamlit dashboard for exploring and visualizing CSV data.

## Features

- **CSV Upload**: Upload any CSV file to analyze
- **Data Explorer**: View, filter, and explore your data
- **Summary Statistics**: Automatic analysis of your dataset
- **Visualizations**: Interactive charts (line, bar, area, scatter)
- **Data Filtering**: Filter by columns and download results

## Setup

The project is already configured! Dependencies are installed via `uv`.

No additional configuration needed - just upload a CSV file to get started.

## Running the Dashboard

From the project directory:

```bash
cd ~/streamlit-dashboard
uv run streamlit run streamlit_app.py
```

Or simply:

```bash
uv run streamlit run streamlit_app.py
```

The dashboard will open in your browser at http://localhost:8501

## Usage

### Getting Started
1. Click the file uploader in the sidebar
2. Select a CSV file from your computer
3. The dashboard will automatically load and analyze your data

### Data Tab
- View your uploaded data in an interactive table
- Select specific columns to display
- Download the data or selected columns

### Summary Tab
- View dataset statistics (rows, columns, missing values)
- See column information and data types
- Analyze numeric column statistics
- Explore categorical value distributions

### Charts Tab
- Create interactive visualizations
- Choose from line, bar, area, or scatter charts
- Select X and Y axes
- Visualizes up to 1,000 rows for performance

### Filter Tab
- Filter by specific columns
- Use range sliders for numeric columns
- Select multiple values for categorical columns
- Download filtered results

## Customization

To customize the dashboard:

1. **Add chart libraries**: Install Plotly or Altair for advanced visualizations
2. **Add metrics**: Use `st.metric()` for KPI cards with sparklines
3. **Change theme**: Create `.streamlit/config.toml` with custom colors
4. **Add more analysis**: Extend with correlation matrices, distributions, etc.

## Dependencies

- `streamlit>=1.53.0` - Dashboard framework
- `pandas` - Data manipulation (installed with Streamlit)

## Troubleshooting

### File Upload Issues
- Ensure the file is in CSV format
- Check that the CSV is properly formatted with headers
- Large files (>200MB) may take longer to load

### Chart Issues
- Charts require numeric columns for Y-axis
- Scatter plots need at least 2 numeric columns
- Only first 1,000 rows are visualized for performance

## Next Steps

Consider adding:
- Advanced visualizations with Plotly or Altair
- Data cleaning and transformation tools
- Statistical analysis (correlations, distributions)
- Machine learning insights
- Export reports as PDF

## Resources

- [Streamlit Documentation](https://docs.streamlit.io)
- [Pandas Documentation](https://pandas.pydata.org/docs/)
