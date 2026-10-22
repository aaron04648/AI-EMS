# Project Report

*The project will be structured using the CRISP-DM framework. Details about the framework can be found [here](https://www.datascience-pm.com/crisp-dm-2/). We will not conduct the deployment step. This page will serve as your project report. The current content provides a rough structure and helps you keep track of the deadlines for each deliverable. The first- and second-level headings should remain as provided in the template. All other content can be freely edited and adapted to your project.*

## Project Details

> 
### Team Members

| Name | Major task |
|--------|------------|
| Aaron Stupf |  Rechner   |
| Sebastian Lischer |  Analyse |



### Project Name

> Intelligent EMS
> 

### Pitch
Dynamic electricity tariffs are coming to Switzerland across most utilities, though they are currently only available in select regions like Zurich via EKZ. Our primary focus is analyzing European market factors to demystify these price swings and accurately simulate day-ahead prices 24 hours in advance. Building on this transparent analysis, an optional calculator demonstrates a practical use case: optimizing energy usage to save up to 0.15 CHF per 15-minute interval—roughly 500 CHF annually—while helping attract more people to solar power and driving overall sustainability.

---

# 1. Business Understanding

## Problem Statement

As Swiss utilities increasingly adopt dynamic 15-minute electricity tariffs, most households remain completely unaware of the underlying European market drivers causing price fluctuations and how to leverage them. Without transparent market analysis and predictive tooling, consumers miss out on significant cost savings and remain hesitant to invest in photovoltaic systems and home batteries.

## Goals

### 1. Core Market Analysis & 24h Price Simulation:
By the end of the semester, we aim to develop and validate a transparent analysis engine processing European day-ahead prices and generation data (via energy-charts.info) to explain price drivers. Success is explicitly measured by achieving a Mean Absolute Error (MAE) of under 10% in our 24-hour day-ahead price forecasts compared to actual market outcomes.

### 2. Optional Predictive Use-Case & Impact Calculator:
 Within the same timeframe, we will implement an optional model predictive control calculator to simulate household PV production and battery storage schedules. This will be measured by its ability to quantify concrete financial benefits—demonstrating savings of up to 0.15 CHF per 15-minute interval (roughly 500 CHF annually)—while successfully proving how such transparency incentivizes solar adoption and enhances ecological sustainability.

### 3. Documentation & Expert Validation:
We will finalize a comprehensive technical report and interactive demonstration by project completion, validating the usability and practical viability of our system through a dedicated presentation and review with the company Plan-E after the final discussion.

# 2. Data Understanding

## Data Sources

> ⚠️ **Deliverable - 12.11.2026:** Define data sources.

*You are free to choose any data source. As a starting point you might consider [Kaggle](https://www.kaggle.com/datasets) or Swiss open data platforms (e.g. Open Government Data [Zurich](https://www.stadt-zuerich.ch/de/politik-und-verwaltung/statistik-und-daten/open-government-data.html), [Luzern](https://www.stadtluzern.ch/opengovernmentdata), or [Switzerland](https://opendata.swiss/de)) as potential data sources.*

| Data source | Description |
|-------------|-------------|
|             |             |
|             |             |


## Exploratory Data Analysis

> ⚠️ **Deliverable - 12.11.2026:** Conduct an exploratory data analysis.

*A hands-on definition of EDA can be found [here](https://www.geeksforgeeks.org/data-analysis/what-is-exploratory-data-analysis/). The implementation of the EDA should be contained in a dedicated notebook.*

- Notebook to run the EDA: [01_data_understanding.ipynb](notebooks/01_data_understanding.ipynb)

### Data Quality

*Short description of the results from the EDA with respect to data quality issues, such as coverage, data types, missing values, etc.*

### Insights

*List insights from the EDA with respect to the problem statement. Is the data suitable for reaching your goals?*

### Visualizations

The entire set of visualizations can be found in `figures/eda`.

---

# 3. Data Preparation
*Data preparation (also known as feature engineering) consists of steps such as cleaning, filtering, joining, [aggregating](https://www.geeksforgeeks.org/python/grouping-and-aggregating-with-pandas/), [normalizing](https://www.geeksforgeeks.org/python/data-normalization-with-pandas/), and transforming the data. The goal is to have one dataset that contains all features and is ready to be used in the follow-up data modeling. All data preparation is contained in one notebook that may use modular code. Data preparation is not a deliverable but goes hand in hand with the follow-up step of data modeling.*

- Notebook to run the data preparation: [02_data_preparation.ipynb](notebooks/02_data_preparation.ipynb)
- Reusable, modular code is outsourced to separate `.py` files: [src/data_preparation.py](src/data_preparation.py)

## Features

*List and describe each feature.*

| Feature | Description |
|---------|-------------|
|         |             |
|         |             |
|         |             |

---

# 4. Data Modeling

> ⚠️ **Deliverable - 03.12.2026:** Summarize the data modeling approach.

## Concept
*Describe the data modeling approach in a few sentences. Which major tasks have to be performed? Which types of models are used (why)? What are the results? The goal of DSPRO1 is not to implement complex ML algorithms but to tell a coherent data story. A dashboard visualisation or a classical statistical analysis cab be sufficient in terms of data modeling.*

## Implementation
*Make a reference to the notebook and scripts used for performing the data modeling. Sketch a diagram that contains all major tasks and notes for the corresponding implementation.*

- Notebook to run the data modeling: [03_modeling.ipynb](notebooks/03_modeling.ipynb)
- Reusable, modular code is outsourced to separate `.py` files: [src/data_modeling.py](src/data_modeling.py)


# 5. Evaluation

> ⚠️ **Deliverable - 10.12.2026:** Discuss your results in the light of the project goals.

- Notebook to run the evaluation: [04_evaluation.ipynb](notebooks/04_evaluation.ipynb)

- All visualizations can be found in `figures/eval`.


## Results
*List major results. Results often come in the form of evaluation metrics.  A short introduction can be found [here](https://www.ibm.com/think/topics/model-evaluation).*

## Interpretation
*Interpret results in the light of the problem statement. Could you solve the problem? An important issue in the interpretation is practical applicability. Is your solution generalizable?*

## Limitations
*Describe weaknesses of your approach or the underlying data.*

---

# 6. Sustainable Development Goals

> ⚠️ **Deliverable - 17.12.2026:** Describe your results in the light of sustainability.

*The United Nations distinguishes 17 Sustainable Development Goals ([link](https://sdgs.un.org/goals)). Put the results of your project in relation to at least one of these goals. A four-sentence description is sufficient.*
